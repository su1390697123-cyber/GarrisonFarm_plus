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


def _install_selection_rules(namespace):
    exec(compile("def passed_count(panel):\n    import re\n    import unicodedata\n    texts=[str(t) for _,y,t in panel if y<350]\n    for i,text in enumerate(texts):\n        if '通过' not in text:\n            continue\n        def normalize(value):\n            return ''.join(str(unicodedata.digit(c)) if c.isdigit() else c for c in value)\n        combined=normalize(text)\n        if i+1<len(texts):\n            following=normalize(texts[i+1]).strip()\n            if re.fullmatch(r'\\d+\\s*次?',following):\n                combined+=following\n        match=re.search(r'通过\\s*(?:模拟)?\\s*(\\d+)\\s*次',combined)\n        if match:\n            return int(match.group(1))\n        return None\n    return 0\n\n\ndef pick_strategy(done):\n    tap(1500,580,1.0)\n    rows=list(GRID_Y)\n    for page in range(8):\n        img=shot()\n        for gy in rows:\n            if not GRID_TOP+60<=gy<=GRID_BOTTOM-40:\n                continue\n            for gx in GRID_X:\n                tap(gx,gy,0.7)\n                count=None\n                name=''\n                for attempt in range(3):\n                    panel=ocr(shot(),(1440,150,1920,800))\n                    # A real strategy panel must be open before absence of a\n                    # completion banner can be interpreted as zero clears.\n                    if any('初始生命值' in str(t).replace(' ','') for _,_,t in panel):\n                        count=passed_count(panel)\n                        name=strategy_name(panel)\n                        if count is not None:\n                            break\n                    if attempt<2:\n                        time.sleep(0.6)\n                if count is None:\n                    log('通关次数读取失败，跳过该格',(gx,gy))\n                    continue\n                if not name:\n                    log('无法识别名称',(gx,gy),'已通过',count,'次')\n                label=name or '无法识别名称[%s,%s]'%(gx,gy)\n                if count>=TARGET:\n                    log('跳过策略:',label,'已通过',count,'次，目标',TARGET,'次')\n                    continue\n                log('选择策略: %s（已通过 %s 次，目标 %s 次）'%(label,count,TARGET))\n                return label\n        scroll_grid()\n        after=shot()\n        shift=grid_shift(img,after)\n        if shift is not None and abs(shift)<20:\n            break\n        rows=detect_rows(after)\n        if not rows:\n            break\n        log('策略列表下滑，继续查找（本页 %s 行）'%len(rows))\n    log('没有找到通关次数未达标的策略')\n    return None\n", 'selection_rules.py', 'exec'), namespace)
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

    session_log('[固定选人修复版] 支持中英文名称；单次点选，识别重试不重复点击；加载目录:',
                namespace.get('HERE', os.path.dirname(os.path.abspath(__file__))))
    locked_name = None

    def return_to_top():
        # Drag the same list region as scroll_grid, in the opposite direction.
        # Resolve touch primitives at call time so SDK touch/fast capture remain
        # active after install() replaces the original ADB implementations.
        stable = 0
        before = namespace['shot']()
        # Keep the legacy first-character path. For other cells, use a short
        # drag so grid_shift can track the same image strip in both frames;
        # a long drag can mistake similar rows for a stationary list.
        end_y = 900 if first_cell else 660
        for _ in range(12 if first_cell else 100):
            try:
                namespace['touch']('DOWN', 1000, 480)
                namespace['drag_path'](1000, 480, 1000, end_y, 14)
                time.sleep(0.15)
            finally:
                namespace['touch']('UP', 1000, end_y)
            time.sleep(0.45)
            after = namespace['shot']()
            shift = namespace['grid_shift'](before, after)
            stable = stable + 1 if shift is not None and abs(shift) < (20 if first_cell else 5) else 0
            if stable >= 2:
                return True
            before = after
        session_log('无法确认角色列表已回到顶部，停止选人，请检查游戏界面。')
        return False

    def locate_target():
        x = namespace['GRID_X'][fixed_col - 1]
        first_y = namespace['GRID_Y'][0]
        row_height = namespace.get('ROW_H', namespace['GRID_Y'][1] - first_y)
        target_y = first_y + (fixed_row - 1) * row_height
        if first_cell:
            return x, first_y
        low = namespace['GRID_TOP'] + 60
        high = namespace['GRID_BOTTOM'] - 60
        image = namespace['shot']()
        offset = 0
        bottom_hits = 0
        # Short steps keep the original grid_shift() template inside its
        # matching region. A fixed number of swipes would miscount at the end
        # of the list or when MuMu moves fewer pixels than requested.
        for _ in range(min(100, fixed_row * 2 + 8)):
            predicted = target_y - offset
            if low <= predicted <= high:
                rows = namespace['detect_rows'](image)
                nearby = [y for y in rows if low <= y <= high and abs(y - predicted) <= row_height * 0.2]
                if len(nearby) != 1:
                    session_log('无法确认目标行的位置，停止选人:', position_label)
                    return None
                return x, nearby[0]
            if predicted < low:
                session_log('滚动位置已越过目标行，停止选人:', position_label)
                return None
            before = image
            try:
                namespace['touch']('DOWN', 1000, 800)
                namespace['drag_path'](1000, 800, 1000, 620, 14)
                time.sleep(0.2)
            finally:
                namespace['touch']('UP', 1000, 620)
            time.sleep(0.45)
            image = namespace['shot']()
            shift = namespace['grid_shift'](before, image)
            if shift is None:
                # Let a late screenshot settle; do not issue another blind drag.
                time.sleep(0.5)
                image = namespace['shot']()
                shift = namespace['grid_shift'](before, image)
            if shift is None or shift < -10 or shift > row_height * 1.4:
                session_log('无法确认列表滚动距离，停止选人:', position_label)
                return None
            offset += shift
            if shift <= 10:
                bottom_hits += 1
                if bottom_hits >= 2:
                    session_log('列表已到底部，未找到指定行，停止选人:', position_label)
                    return None
            else:
                bottom_hits = 0
        session_log('定位指定角色超过滚动上限，停止选人:', position_label)
        return None

    def pick_fixed_strategy(done):
        nonlocal locked_name
        namespace['tap'](1500, 580, 1.0)
        if not return_to_top():
            return None
        position = locate_target()
        if position is None:
            return None
        x, y = position
        # Repeated clicks can toggle details and shift the grid horizontally.
        # Retrying that coordinate can then select another card.
        # Select once; retry only the read while the details/OCR settle.
        namespace['tap'](x, y, 0.7)
        failure_reason = '详情面板未识别到初始生命值'
        for attempt in range(3):
            if attempt:
                time.sleep(0.15)
            panel = namespace['ocr'](namespace['shot'](), (1440, 150, 1920, 800))
            if not any('初始生命值' in str(text).replace(' ', '') for _, _, text in panel):
                failure_reason = '详情面板未识别到初始生命值'
                continue
            name = _fixed_strategy_name(panel, namespace['strategy_name'])
            if not first_cell and not name:
                failure_reason = '名称栏未读到有效角色名：' + repr([str(t) for _, py, t in panel if 520 < py < 650])
                continue
            if locked_name and name and name != locked_name:
                # Never silently choose another character when recognition or
                # the game list differs from the first successful selection.
                failure_reason = '角色名与本次锁定目标不一致：%s / %s' % (name, locked_name)
                continue
            if name:
                locked_name = name
            label = locked_name or position_label
            session_log('固定角色详情识别:', label, '请求位置：第 %s 行第 %s 列' % (fixed_row, fixed_col), '（忽略角标、通关次数及历史完成记录）')
            return label
        session_log('未能确认%s，停止选人；不会跳到其他角色。原因：%s' % (position_label, failure_reason))
        return None

    namespace['pick_strategy'] = pick_fixed_strategy
    # Fixed farming must not mark a character complete just because it was used.
    namespace['save_done'] = lambda done: None
