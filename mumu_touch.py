"""MuMu SDK touch adapter; loaded after the original GarrisonFarm setup."""
import atexit
import ctypes
import json
import os
import time


class MuMuTouch:
    def __init__(self, config, device):
        self.handle = 0
        self.held = False
        self.display = None
        self.last = None
        self.interval = max(0.008, float(config.get('move_interval', 0.016)))
        root = config['emulator_path']
        path = config.get('sdk_path') or os.path.join(root, 'nx_main', 'sdk', 'external_renderer_ipc.dll')
        if not os.path.isfile(path):
            raise RuntimeError('MuMu SDK 不存在: ' + path)
        self.dll = ctypes.CDLL(path)
        signatures = {
            'nemu_connect': [ctypes.c_wchar_p, ctypes.c_int],
            'nemu_disconnect': [ctypes.c_int],
            'nemu_get_display_id': [ctypes.c_int, ctypes.c_char_p, ctypes.c_int],
            'nemu_capture_display': [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_void_p],
            'nemu_input_event_finger_touch_down': [ctypes.c_int]*5,
            'nemu_input_event_finger_touch_up': [ctypes.c_int]*3,
        }
        for name, args in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes = args
            fn.restype = ctypes.c_int
        index = config.get('instance_index')
        if index is None:
            port = int(str(device).rsplit(':', 1)[1])
            if port < 16384 or (port-16384) % 32:
                raise RuntimeError('无法由 ADB 地址确定 MuMu 实例，请设置 instance_index')
            index = (port-16384)//32
        self.index = int(index)
        try:
            self.handle = self.dll.nemu_connect(root, self.index)
            if not self.handle:
                raise RuntimeError('MuMu SDK 连接失败，实例 ' + str(self.index))
            candidates = config.get('_package_candidates') or [config.get('package_name', 'com.hypergryph.arknights')]
            matches = []
            for package in candidates:
                display = self.dll.nemu_get_display_id(self.handle, package.encode('utf-8'), 0)
                if display >= 0:
                    matches.append((package, display))
            if not matches:
                raise RuntimeError('未找到明日方舟画面，请打开官服或B服游戏')
            if len(matches) > 1:
                raise RuntimeError('官服和B服都在运行但无法确定前台，请把要刷的游戏切到前台后重试')
            self.package, self.display = matches[0]
            print('[自动检测] 当前游戏: ' + self.package, flush=True)
            w, h = ctypes.c_int(), ctypes.c_int()
            ret = self.dll.nemu_capture_display(self.handle, self.display, 0, ctypes.byref(w), ctypes.byref(h), None)
            if ret or (w.value, h.value) != (1920, 1080):
                raise RuntimeError('MuMu 游戏画面尺寸不匹配: %sx%s (ret=%s)' % (w.value, h.value, ret))
            self.width, self.height = w.value, h.value
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            try:
                if self.held:
                    self.dll.nemu_input_event_finger_touch_up(self.handle, self.display, 1)
            finally:
                self.held = False
                self.dll.nemu_disconnect(self.handle)
                self.handle = 0

    def _point(self, x, y):
        x, y = int(x), int(y)
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError('触控坐标超出画面: %s,%s' % (x, y))
        return x, y

    def _down(self, x, y):
        x, y = self._point(x, y)
        self.held = True  # Release even if the native call reports a partial failure.
        ret = self.dll.nemu_input_event_finger_touch_down(self.handle, self.display, 1, x, y)
        if ret:
            self._abort('MuMu 触控失败: ' + str(ret))
        self.last = (x, y)

    def _up(self):
        if self.held:
            ret = self.dll.nemu_input_event_finger_touch_up(self.handle, self.display, 1)
            if ret:
                self._abort('MuMu 松手失败: ' + str(ret))
            self.held = False

    def _abort(self, message):
        try:
            self.close()
        finally:
            raise RuntimeError(message + '；已停止，避免在拖动中切换触控后端')

    def touch(self, kind, x, y):
        kind = str(kind).upper()
        if kind == 'DOWN':
            if self.held:
                self._up()
            self._down(x, y)
            time.sleep(0.06)
        elif kind == 'MOVE':
            if not self.held:
                raise RuntimeError('MOVE 前没有按下')
            self._down(x, y)
        elif kind == 'UP':
            self._up()
        else:
            raise ValueError('未知触控事件: ' + kind)

    def drag_path(self, x1, y1, x2, y2, steps=12):
        if not self.held:
            raise RuntimeError('拖动前没有按下')
        self._point(x1, y1)
        self._point(x2, y2)
        steps = max(1, int(steps))
        try:
            for i in range(1, steps+1):
                self._down(x1+(x2-x1)*i/steps, y1+(y2-y1)*i/steps)
                time.sleep(self.interval)
        except BaseException:
            self._up()
            raise

    def tap(self, x, y, wait=0.6):
        try:
            self.touch('DOWN', x, y)
        finally:
            self._up()
        time.sleep(wait)

    def swipe(self, x1, y1, x2, y2, ms=500, wait=0.8):
        try:
            self.touch('DOWN', x1, y1)
            steps = max(1, round(float(ms)/1000/self.interval))
            self.drag_path(x1, y1, x2, y2, steps)
        finally:
            self._up()
        time.sleep(wait)


def install(namespace):
    """Only swap all four primitives after successful connection and validation."""
    _install_selection_rules(namespace)
    _install_session_options(namespace)
    path = os.path.join(namespace['HERE'], 'mumu_touch.json')
    try:
        with open(path, encoding='utf-8-sig') as f:
            config = json.load(f)
        if not config.get('enabled', True):
            print('[增强触控] 已关闭，使用原 ADB 触控', flush=True)
            return
        config = _resolve_environment(config, namespace)
        controller = MuMuTouch(config, config['_device'])
    except Exception as exc:
        print('[增强触控] 初始化失败，保留原 ADB 触控: ' + str(exc), flush=True)
        return
    atexit.register(controller.close)
    namespace['_mumu_touch_controller'] = controller
    namespace.update({name: getattr(controller, name) for name in ('tap', 'swipe', 'touch', 'drag_path')})
    enable_fast_capture(controller, namespace, config)
    print('[增强触控] MuMu SDK 已启用，实例 %s，display %s；保留原截图和 OCR' % (controller.index, controller.display), flush=True)


def probe(namespace):
    """Read-only SDK connection check; never sends touch events."""
    with open(os.path.join(namespace['HERE'], 'mumu_touch.json'), encoding='utf-8-sig') as f:
        config = json.load(f)
    namespace = dict(namespace)
    if os.environ.get('GF_CHECK_ADB'):
        namespace['ADB'] = os.environ['GF_CHECK_ADB']
    if os.environ.get('GF_CHECK_ADDRESS'):
        namespace['DEV'] = os.environ['GF_CHECK_ADDRESS']
    config = _resolve_environment(config, namespace)
    controller = MuMuTouch(config, config['_device'])
    try:
        print('[增强触控检查] SDK 连接成功，实例 %s，display %s，画面 %sx%s；未发送触控事件' % (controller.index, controller.display, controller.width, controller.height), flush=True)
    finally:
        controller.close()




def enable_fast_capture(controller, namespace, config):
    import threading
    lock = threading.RLock()
    np = namespace['np']
    width, height = controller.width, controller.height
    buffer = (ctypes.c_ubyte * (width*height*4))()

    def capture():
        with lock:
            w, h = ctypes.c_int(width), ctypes.c_int(height)
            ret = controller.dll.nemu_capture_display(controller.handle, controller.display, len(buffer), ctypes.byref(w), ctypes.byref(h), buffer)
            if ret or (w.value,h.value)!=(width,height):
                raise RuntimeError('MuMu SDK 截图失败或尺寸改变: '+str(ret))
            rgba=np.ctypeslib.as_array(buffer).reshape(height,width,4)
            return np.ascontiguousarray(rgba[::-1,:,2::-1])

    if config.get('fast_capture',True):
        namespace['shot']=capture
        print('[快速截图] MuMu SDK 已启用，替代 ADB 截图',flush=True)


def _sdk_location(hint):
    if not hint:
        return None
    hint=os.path.abspath(os.path.expandvars(str(hint).strip('"')))
    directory=hint if os.path.isdir(hint) else os.path.dirname(hint)
    for _ in range(7):
        for rel in ('nx_main/sdk/external_renderer_ipc.dll','nx_device/15.0/shell/sdk/external_renderer_ipc.dll','nx_device/12.0/shell/sdk/external_renderer_ipc.dll','shell/sdk/external_renderer_ipc.dll','sdk/external_renderer_ipc.dll'):
            sdk=os.path.join(directory,*rel.split('/'))
            if os.path.isfile(sdk):
                # When starting from nx_main or a version shell, prefer an actual installation root.
                if rel.startswith('sdk/') and os.path.basename(directory).lower() in ('nx_main','shell'):
                    continue
                root=directory
                if os.path.basename(os.path.dirname(root)).lower()=='nx_device':
                    root=os.path.dirname(os.path.dirname(root))
                return root,sdk
        parent=os.path.dirname(directory)
        if parent==directory:break
        directory=parent
    return None


def _read_command(args,timeout=5):
    import subprocess
    try:
        r=subprocess.run(args,capture_output=True,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return r.stdout.decode('utf-8',errors='replace') if r.returncode==0 else ''
    except (OSError,subprocess.TimeoutExpired):
        return ''


def _running_mumu_paths():
    text=_read_command(['powershell.exe','-NoProfile','-Command',"[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Process MuMuNxDevice,MuMuPlayer,MuMuPlayerService -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"],timeout=6)
    return [s.strip() for s in text.splitlines() if s.strip()]


def _maa_connections(namespace):
    try:
        maa=namespace['find_maa']()
        if maa:
            adb,address,_,_=namespace['read_maa_connect'](maa)
            return adb,address
    except Exception:
        pass
    return '',''


def _resolve_environment(config,namespace):
    import re
    result=dict(config)
    adb=namespace.get('ADB') or ''
    device=namespace.get('DEV') or ''
    maa_adb,maa_device=('','')
    if not adb or not device:
        maa_adb,maa_device=_maa_connections(namespace)
        adb=adb or maa_adb
        device=device or maa_device
    # The already-connected ADB is the strongest installation hint.
    location=_sdk_location(adb)
    if location is None:
        locations=[]
        for hint in _running_mumu_paths():
            candidate=_sdk_location(hint)
            if candidate and candidate not in locations:locations.append(candidate)
        if len(locations)==1:location=locations[0]
        elif len(locations)>1:
            manual=_sdk_location(config.get('emulator_path'))
            if manual in locations:location=manual
            else:raise RuntimeError('检测到多套正在运行的MuMu，请在MAA中设置目标实例的ADB路径')
    if location is None:
        for drive in 'CDEFGHIJK':
            for suffix in ('MuMuPlayer','Program Files/Netease/MuMu','Program Files/Netease/MuMuPlayer-12.0','Program Files/MuMuPlayer-12.0'):
                location=_sdk_location(drive+':/'+suffix)
                if location:break
            if location:break
    if location is None:location=_sdk_location(config.get('emulator_path'))
    if location is None:
        raise RuntimeError('未找到MuMu SDK，请打开模拟器并确认MAA连接；也可填写emulator_path')
    root,sdk=location
    result['emulator_path']=root
    result['sdk_path']=sdk
    if not adb:
        for rel in ('nx_main/adb.exe','nx_device/15.0/shell/adb.exe','nx_device/12.0/shell/adb.exe','shell/adb.exe'):
            candidate=os.path.join(root,*rel.split('/'))
            if os.path.isfile(candidate):adb=candidate;break
    if not device and adb:
        devices=[]
        for line in _read_command([adb,'devices']).splitlines():
            parts=line.split()
            if len(parts)==2 and parts[1]=='device':devices.append(parts[0])
        if len(devices)==1:device=devices[0]
    if not device:
        raise RuntimeError('未确定目标模拟器实例，请先在MAA中连接目标模拟器')
    result['_device']=device
    packages=[]
    foreground=''
    if adb:
        def shell(*args):return _read_command([adb,'-s',device,'shell']+list(args))
        for command in (('dumpsys','activity','activities'),('dumpsys','window','windows')):
            text=shell(*command)
            for line in text.splitlines():
                if any(key in line for key in ('mResumedActivity','topResumedActivity','mCurrentFocus','mFocusedApp')):
                    hit=re.search(r'([A-Za-z][\w]*(?:\.[\w]+)+)/',line)
                    if hit and 'arknights' in hit[1].lower():foreground=hit[1];break
            if foreground:break
        for line in shell('pm','list','packages').splitlines():
            package=line.removeprefix('package:').strip()
            if re.fullmatch(r'[\w.]+',package) and 'arknights' in package.lower() and package not in packages:packages.append(package)
    configured=config.get('package_name','auto')
    if foreground:
        result['_package_candidates']=[foreground]
    elif packages:
        result['_package_candidates']=packages
    elif configured and configured!='auto':
        result['_package_candidates']=[configured]
    else:
        result['_package_candidates']=['com.hypergryph.arknights','com.hypergryph.arknights.bilibili']
    print('[自动检测] MuMu路径: '+root,flush=True)
    return result


def _detect_strategy_cards(image, cv2, np):
    """Find 190px strategy cards in either detail-panel layout, without OCR."""
    small = cv2.resize(image, (960, 540), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    raw = cv2.Canny(gray, 20, 60)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 15, 40)
    for mask in (raw, edges):
        mask[:70] = 0
        mask[:, :270] = 0
        mask[:, 915:] = 0
    horizontal = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((1, 28), np.uint8))
    vertical = cv2.morphologyEx(edges, cv2.MORPH_OPEN, np.ones((28, 1), np.uint8))
    lines = cv2.morphologyEx(horizontal | vertical, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    seeds = []
    for mask in (raw, lines):
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if 83 <= w <= 106 and 83 <= h <= 106 and abs(w-h) <= 9:
                cx, cy = x+w/2, y+h/2
                if not any(abs(cx-a) < 7 and abs(cy-b) < 10 for a,b in seeds):
                    seeds.append((cx, cy))
    # A selected card has separate green corner brackets instead of a closed
    # grey rectangle. Pair its tall left/right brackets as an additional seed.
    b,g,r = [small[:,:,i].astype(np.float32) for i in range(3)]
    green = ((g>125)&(g>r*1.5)&(g>b*1.08)).astype(np.uint8)*255
    green[:75] = 0
    green[:,:270] = 0
    green[:,915:] = 0
    green = cv2.morphologyEx(green,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    contours,_ = cv2.findContours(green,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    green_boxes = [cv2.boundingRect(c) for c in contours]
    corners = [(x,y,w,h) for x,y,w,h in green_boxes if 8<=w<=25 and 8<=h<=25]
    for x,y,w,h in corners:
        for rx,ry,rw,rh in corners:
            width = rx+rw-x
            if not (86<=width<=105 and abs(ry-y)<=5):
                continue
            left_bottom = [(bx,by,bw,bh) for bx,by,bw,bh in corners if abs(bx-x)<=5 and 86<=by+bh-y<=105]
            right_bottom = [(bx,by,bw,bh) for bx,by,bw,bh in corners if abs(bx-rx)<=5 and 86<=by+bh-ry<=105]
            for lx,ly,lw,lh in left_bottom:
                for bx,by,bw,bh in right_bottom:
                    if abs(ly-by)<=5:
                        seeds.append((x+width/2,(y+ry+ly+lh+by+bh)/4))
    brackets = [(x,y,w,h) for x,y,w,h in green_boxes if 8<=w<=23 and 60<=h<=104]
    for x,y,w,h in brackets:
        for rx,ry,rw,rh in brackets:
            width = rx+rw-x
            if 86<=width<=105 and abs(ry-y)<=5 and abs(rh-h)<=6:
                cy = (y+ry+max(h,rh))/2
                # A bracket clipped at the bottom still has a known card top.
                if y+h>=528 and h<86:
                    cy = (y+ry)/2+47.5
                cx = x+width/2
                if not any(abs(cx-a)<7 and abs(cy-b)<10 for a,b in seeds):
                    seeds.append((cx,cy))
    choices = []
    for name, first in (('open', 340), ('closed', 540)):
        matching = []
        for x,y in seeds:
            col = round((x-first)/105)
            error = abs(x-(first+105*col))
            if 0 <= col < 4 and error <= 18:
                matching.append((x,y,col,error))
        score = sum(1-error/22 for x,y,col,error in matching)
        choices.append((score, name, first, matching))
    _, layout, first, matching = max(choices, key=lambda item:item[0])
    if not matching:
        return None
    # Calibrate small horizontal offsets from the actual card borders.
    pitches = [(b[0]-a[0])/(b[2]-a[2]) for a in matching for b in matching if b[2]>a[2] and abs(a[1]-b[1])<12]
    pitch = float(np.median(pitches)) if pitches else 105.
    pitch = min(108., max(103., pitch))
    first = float(np.median([x-col*pitch for x,y,col,error in matching]))
    xs = [first+pitch*col for col in range(4)]
    candidates = [y for x,y,col,error in matching]
    for x in xs:
        left, right = int(x-42), int(x+42)
        on = (horizontal[:,left:right]>0).mean(axis=1) > .45
        ys = np.flatnonzero(on)
        for group in np.split(ys, np.flatnonzero(np.diff(ys)>1)+1):
            if len(group):
                edge_y = float(np.median(group))
                candidates.extend((edge_y-47.5, edge_y+47.5))

    def support(mask, x0,y0,x1,y1,axis):
        part = mask[max(70,int(y0)):min(533,int(y1)),max(270,int(x0)):min(915,int(x1))]
        return float((part>0).any(axis=axis).mean()) if part.size else 0.

    def border(x,y):
        left = support(vertical,x-51,y-38,x-43,y+38,1)
        right = support(vertical,x+43,y-38,x+51,y+38,1)
        top = support(horizontal,x-38,y-51,x+38,y-43,0)
        bottom = support(horizontal,x-38,y+43,x+38,y+51,0)
        raw_left = support(edges,x-52,y-38,x-42,y+38,1)
        raw_right = support(edges,x+42,y-38,x+52,y+38,1)
        valid = min(raw_left,raw_right) >= .7 and max(left,right) >= .58 and max(top,bottom) >= .50
        corner_counts = []
        for dx in (-47.5,47.5):
            for dy in (-47.5,47.5):
                patch = green[max(75,int(y+dy-12)):min(533,int(y+dy+12)),int(x+dx-6):int(x+dx+6)]
                corner_counts.append(int((patch>0).sum())>=8)
        highlighted = corner_counts[0] and corner_counts[2] and (corner_counts[1] or corner_counts[3] or y+47.5>=523)
        return valid or highlighted, left+right+top+bottom+(4 if highlighted else 0)

    rows = []
    for y in candidates:
        if not 115 <= y <= 525:
            continue
        cells = {}
        score = 0.
        for col,x in enumerate(xs,1):
            valid, strength = border(x,y)
            seed = any(abs(x-sx)<9 and abs(y-sy)<9 for sx,sy,_,_ in matching)
            if valid or seed:
                cells[col] = (round(x*2), round(y*2))
                score += strength
        if not cells:
            continue
        previous = next((r for r in rows if abs(r['y']-y*2)<140), None)
        row = dict(y=round(y*2), cells=cells, score=score)
        if previous is None:
            rows.append(row)
        elif score > previous['score']:
            rows[rows.index(previous)] = row
    rows.sort(key=lambda row:row['y'])
    return dict(layout=layout, rows=rows, xs=[round(x*2) for x in xs])


class _StrategyGrid:
    """Selection-only vision and scrolling. Touch/OCR are resolved at call time."""
    def __init__(self, namespace):
        self.ns = namespace
        self.cv2, self.np = namespace['cv2'], namespace['np']

    def frame(self, image=None):
        image = self.ns['shot']() if image is None else image
        result = _detect_strategy_cards(image, self.cv2, self.np)
        if not result or not result['rows']:
            raise ValueError('未识别到角色卡片，请停在选择策略界面')
        result['image'] = image
        return result

    @staticmethod
    def visible(frame):
        # Count complete rows only. A clipped final row is revisited after a scroll.
        return [row for row in frame['rows'] if 250 <= row['y'] <= 950]

    def selected(self, frame, col, y):
        if frame['layout'] != 'open':
            return False
        image = frame['image']
        x = frame['xs'][col-1]
        corners = []
        for dx in (-95, 95):
            for dy in (-95, 95):
                patch = image[max(150,y+dy-24):min(1065,y+dy+24), x+dx-12:x+dx+12]
                if not patch.size:
                    corners.append(False)
                    continue
                b,g,r = [patch[:,:,i].astype(self.np.float32) for i in range(3)]
                corners.append(int(((g>130)&(g>r*1.5)&(g>b*1.1)).sum()) >= 35)
        # A neighbouring row's top corners must not look like this row's bottom.
        return corners[0] and corners[2] and (corners[1] or corners[3] or y+95>=1045)

    def motion(self, before, after, downward):
        if before['layout'] != after['layout']:
            return None
        cv2, np = self.cv2, self.np
        # Downsampling suppresses the screen-space scan lines on transparent cards.
        def gray(image):
            small = cv2.resize(image,(960,540),interpolation=cv2.INTER_AREA)
            return cv2.GaussianBlur(cv2.cvtColor(small,cv2.COLOR_BGR2GRAY),(3,3),0)
        old_gray, new_gray = gray(before['image']), gray(after['image'])
        matches = []
        for row in self.visible(before):
            for col,(x,y) in sorted(row['cells'].items()):
                cx,cy = round(x/2),round(y/2)
                nx = round(after['xs'][col-1]/2)
                patch = old_gray[cy-18:cy+18,cx-29:cx+29]
                # Card-border calibration can differ by a few pixels per frame.
                # Search both axes, including movement larger than the finger drag.
                top,bottom = 75,530
                left,right = max(0,nx-41),min(960,nx+41)
                area = new_gray[top:bottom,left:right]
                if area.shape[0]<36 or area.shape[1]<58 or patch.std()<6:
                    continue
                scores = cv2.matchTemplate(area,patch,cv2.TM_CCOEFF_NORMED)
                _,strength,_,loc = cv2.minMaxLoc(scores)
                delta = 2*(cy-(top+loc[1]+18))
                if strength>=.72 and abs(delta)<=800:
                    matches.append((float(strength),delta,(row['y'],col)))
        self._motion_matches = [(round(s,3),d) for s,d,_ in matches]
        if not matches:
            return None
        # Group independent card matches; favour image agreement over a count
        # of similar-looking portraits that have already left the viewport.
        clusters = []
        for strength,delta,key in matches:
            if any(abs(delta-center)<=12 for _,_,center in clusters):
                continue
            group = [(s,d,k) for s,d,k in matches if abs(d-delta)<=12]
            quality = float(np.mean(sorted((s for s,_,_ in group),reverse=True)[:3]))
            clusters.append((quality,group,delta))
        candidates = [item for item in clusters if len(item[1])>=2 or item[0]>=.94]
        if not candidates:
            return None
        quality,group,_ = max(candidates,key=lambda item:item[0])
        displacement = int(round(float(np.median([d for _,d,_ in group]))))
        if not (-8<=displacement<=800 if downward else -800<=displacement<=8):
            return None
        rivals = [q for q,g,d in candidates if abs(d-displacement)>12]
        if rivals and quality-max(rivals)<.008:
            return None
        if len(group)>=2 and quality>=.78:
            return displacement
        if quality>=.94:
            return displacement
        return None

    def _scroll_diagnostic(self, before, after, downward, distance):
        root = self.ns.get('HERE')
        if not root:
            return
        try:
            folder = os.path.join(root,'farm_logs')
            os.makedirs(folder,exist_ok=True)
            for name,frame in (('before',before),('after',after)):
                self.cv2.imencode('.png',frame['image'])[1].tofile(os.path.join(folder,'selection_scroll_'+name+'.png'))
            data = {'downward':downward,'finger_distance':distance,
                    'matches':getattr(self,'_motion_matches',[]),
                    'before':{k:before[k] for k in ('layout','xs','rows')},
                    'after':{k:after[k] for k in ('layout','xs','rows')}}
            with open(os.path.join(folder,'selection_scroll.json'),'w',encoding='utf-8') as stream:
                json.dump(data,stream,ensure_ascii=False,indent=2)
        except Exception as exc:
            self.ns['log']('选人诊断截图保存失败:',str(exc))

    def scroll(self, frame, downward, distance=300, strict=True):
        x = frame['xs'][1]+65
        y1,y2 = (900,900-distance) if downward else (480,480+distance)
        try:
            self.ns['touch']('DOWN',x,y1)
            self.ns['drag_path'](x,y1,x,y2,14)
            time.sleep(.2 if downward else .15)
        finally:
            self.ns['touch']('UP',x,y2)
        time.sleep(.45)
        after = self.frame()
        shift = self.motion(frame,after,downward)
        if shift is None:
            time.sleep(.15)
            after = self.frame()
            shift = self.motion(frame,after,downward)
        if shift is None:
            self._scroll_diagnostic(frame,after,downward,distance)
            if strict:
                raise ValueError('无法确认角色列表滚动距离，停止固定选人；已保存 selection_scroll 前后截图')
            self.ns['log']('滚动位移暂未确认，重新识别当前页，继续按通关次数筛选角色。')
        return after,shift


    def top(self, frame):
        stable = 0
        for _ in range(100):
            frame, shift = self.scroll(frame,False,distance=420)
            stable = stable+1 if abs(shift)<=5 else 0
            if stable>=2:
                return frame
        raise ValueError('无法确认角色列表已回到顶部')

    def target(self, frame, row_number, col):
        known = []
        offset = 0
        for _ in range(100):
            visible = self.visible(frame)
            for row in visible:
                absolute = row['y']+offset
                if not any(abs(absolute-y)<=45 for y in known):
                    known.append(absolute)
            known.sort()
            if len(known)>=row_number:
                absolute = known[row_number-1]
                row = next((r for r in visible if abs(r['y']+offset-absolute)<=45),None)
                if row is None:
                    raise ValueError('目标行已越过可点击范围，请重新开始选人')
                if col not in row['cells']:
                    raise ValueError('第 %s 行没有识别到第 %s 列角色，该位置可能为空；停止选人' % (row_number,col))
                return frame, row['y']
            frame, shift = self.scroll(frame,True)
            offset += shift
            if shift<=5:
                # One final scan is needed when a short bottom scroll exposes a row.
                for row in self.visible(frame):
                    absolute = row['y']+offset
                    if not any(abs(absolute-y)<=45 for y in known):
                        known.append(absolute)
                if len(known)<row_number:
                    raise ValueError('列表已到底部，未找到第 %s 行角色' % row_number)
        raise ValueError('定位指定角色超过滚动上限')

    def read_card(self, frame, col, y, click=True):
        # Do not click an already selected card: that closes its detail panel.
        if click and not self.selected(frame,col,y):
            self.ns['tap'](frame['xs'][col-1],y,.7)
            image = self.ns['shot']()
        else:
            image = frame['image']
        for attempt in range(3):
            if attempt:
                time.sleep(.15)
                image = self.ns['shot']()
            try:
                current = self.frame(image)
            except ValueError:
                continue
            row = next((r for r in current['rows'] if abs(r['y']-y)<=45 and col in r['cells']),None)
            if not row or not self.selected(current,col,row['y']):
                continue
            panel = self.ns['ocr'](image,(1440,150,1920,800))
            if any('初始生命值' in str(t).replace(' ','') for _,_,t in panel):
                return current,panel
        raise ValueError('未能确认目标角色的选中框和详情面板；停止选人，不重复点击或改选其他角色')

    def ordinary(self):
        frame = self.frame()
        visited = []
        offset = 0
        bottom = False
        for _ in range(500):
            candidates = [(row,col) for row in self.visible(frame) for col in sorted(row['cells'])
                          if not any(c==col and abs(y-(row['y']+offset))<=45 for y,c in visited)]
            if not candidates:
                if bottom:
                    break
                frame,shift = self.scroll(frame,True,strict=False)
                if shift is None:
                    # Ordinary mode uses each card's own clear count; no global
                    # row index is needed. Discard stale coordinates after a scroll.
                    visited = []
                    offset = 0
                    bottom = False
                else:
                    offset += shift
                    bottom = shift<=5
                continue
            row,col = candidates[0]
            visited.append((row['y']+offset,col))
            frame,panel = self.read_card(frame,col,row['y'])
            count = self.ns['passed_count'](panel)
            if count is None:
                # Retry the read only; never toggle the selected card.
                for _ in range(2):
                    time.sleep(.15)
                    frame,panel = self.read_card(self.frame(),col,row['y'],click=False)
                    count = self.ns['passed_count'](panel)
                    if count is not None:
                        break
            if count is None:
                self.ns['log']('通关次数读取失败，跳过该角色')
                continue
            name = _fixed_strategy_name(panel,self.ns['strategy_name'])
            label = name or '无法识别名称[%s,%s]' % (col,row['y']+offset)
            if count>=self.ns['TARGET']:
                self.ns['log']('跳过策略:',label,'已通过',count,'次，目标',self.ns['TARGET'],'次')
                continue
            self.ns['log']('选择策略: %s（已通过 %s 次，目标 %s 次）' % (label,count,self.ns['TARGET']))
            return label
        self.ns['log']('没有找到通关次数未达标的策略')
        return None


def _strategy_passed_count(panel):
    import re
    import unicodedata
    texts=[str(t) for _,y,t in panel if y<350]
    for i,text in enumerate(texts):
        if '通过' not in text:
            continue
        def normalize(value):
            return ''.join(str(unicodedata.digit(c)) if c.isdigit() else c for c in value)
        combined=normalize(text)
        if i+1<len(texts):
            following=normalize(texts[i+1]).strip()
            if re.fullmatch(r'\d+\s*次?',following):
                combined+=following
        match=re.search(r'通过\s*(?:模拟)?\s*(\d+)\s*次',combined)
        if match:
            return int(match.group(1))
        return None
    return 0


def _install_selection_rules(namespace):
    namespace['passed_count'] = _strategy_passed_count
    def pick_strategy(done):
        try:
            return _StrategyGrid(namespace).ordinary()
        except ValueError as exc:
            namespace['log']('选人停止:', str(exc))
            return None
    namespace['pick_strategy'] = pick_strategy
    original_save = namespace.get('save_done')
    if original_save:
        def save_known_names(done):
            return original_save({name for name in done if not str(name).startswith('无法识别名称[')})
        namespace['save_done'] = save_known_names


def _fixed_strategy_name(panel, original_name):
    """Accept English strategy names in the existing title band, e.g. Touch.

    The original extractor requires a CJK character and discards English-only
    names even when OCR reads them correctly. Reuse the same OCR result.
    """
    name = original_name(panel)
    if name:
        return name
    import re
    labels = {'TEAMID', 'INFO', 'STRATEGY', 'SELECTED', 'COUNTDOWN'}
    candidates = []
    for x, y, text in panel:
        text = str(text).strip()
        if not 520 < y < 650 or x < 1440:
            continue
        if re.sub(r'[^A-Za-z]', '', text).upper() in labels:
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9 .\-'’·]{0,63}", text):
            candidates.append((abs(y - 580), text))
    return min(candidates)[1] if candidates else ''


def _install_session_options(namespace):
    """Add per-process options before installing the unchanged SDK primitives."""
    import re
    fixed_first = os.environ.get('GF_FIXED_FIRST') == '1'
    fixed_row, fixed_col = 1, 1
    if fixed_first:
        try:
            fixed_row = int(os.environ.get('GF_FIXED_ROW', '1'))
            fixed_col = int(os.environ.get('GF_FIXED_COL', '1'))
            if fixed_row < 1 or not 1 <= fixed_col <= 4:
                raise ValueError('row/column outside the grid')
        except ValueError:
            # The EXE catches ordinary adapter exceptions and falls back to
            # its stock selector. Invalid fixed settings must stop instead.
            raise SystemExit('固定角色位置无效：行号必须为正整数，列号必须为 1～4。')
    first_cell = (fixed_row, fixed_col) == (1, 1)
    position_label = '列表第一角色' if first_cell else '第 %s 行第 %s 列角色' % (fixed_row, fixed_col)
    raw_limit = os.environ.get('GF_TOTAL_ATTEMPTS', '')
    attempt_limit = int(raw_limit) if raw_limit.isdecimal() and int(raw_limit) > 0 else None
    original_log = namespace['log']
    result_pattern = re.compile(
        r'^本局(通关|失败)，策略 (.*?)，用时 (\d+)s，已通关 (\d+)/(\d+)，已打 (\d+) 局$'
    )

    def session_log(*parts):
        if attempt_limit and len(parts) == 1 and isinstance(parts[0], str):
            match = result_pattern.fullmatch(parts[0])
            if match:
                outcome, role, seconds, wins, _, completed = match.groups()
                losses = int(completed) - int(wins)
                original_log(
                    '本局%s，策略 %s，用时 %ss；本次已完成 %s/%s 局，胜利 %s 局，失败 %s 局'
                    % (outcome, role, seconds, completed, attempt_limit, wins, losses)
                )
                if int(completed) >= attempt_limit:
                    original_log('已达到本次总局数，结算返回主页后停止，不再开启下一局。')
                return
        if fixed_first and parts and parts[0] == '已获得角标的策略:':
            original_log('固定角色模式结束；原有本地角标记录保持不变。')
            return
        original_log(*parts)

    namespace['log'] = session_log
    session_log('本次刷取方式:', '固定%s（忽略角标）' % position_label if fixed_first else '普通刷角标')
    if attempt_limit:
        session_log('本次总局数:', attempt_limit, '局；通关和失败均计数，异常中止不计为结算。')
    if not fixed_first:
        return

    session_log('[分组与不满行修复版] 按实际角色行计数，分类标题不算行；单次点选；加载目录:',
                namespace.get('HERE', os.path.dirname(os.path.abspath(__file__))))
    locked_name = None

    def pick_fixed_strategy(done):
        nonlocal locked_name
        grid = _StrategyGrid(namespace)
        try:
            frame = grid.top(grid.frame())
            frame, y = grid.target(frame, fixed_row, fixed_col)
            frame, panel = grid.read_card(frame, fixed_col, y)
            failure_reason = '名称栏未读到有效角色名'
            for attempt in range(3):
                if attempt:
                    time.sleep(.15)
                    frame, panel = grid.read_card(grid.frame(), fixed_col, y, click=False)
                name = _fixed_strategy_name(panel, namespace['strategy_name'])
                if not first_cell and not name:
                    continue
                if locked_name and name and name != locked_name:
                    failure_reason = '角色名与本次锁定目标不一致：%s / %s' % (name, locked_name)
                    continue
                if name:
                    locked_name = name
                label = locked_name or position_label
                session_log('固定角色详情识别:', label, '请求位置：第 %s 行第 %s 列' % (fixed_row, fixed_col), '（忽略角标、通关次数及历史完成记录）')
                return label
            session_log('未能确认%s，停止选人；不会跳到其他角色。原因：%s' % (position_label, failure_reason))
        except ValueError as exc:
            session_log('固定选人停止:', str(exc))
        return None

    namespace['pick_strategy'] = pick_fixed_strategy
    # Fixed farming must not mark a character complete just because it was used.
    namespace['save_done'] = lambda done: None
