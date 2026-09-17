# Kachaka App UI Screens (zh-TW, firmware 3.18.1)

Screen-by-screen map of the Kachaka app, recorded by driving the macOS app
against a Kachaka Pro on firmware 3.18.1 (2026-09-17). Labels are quoted
verbatim from the Traditional Chinese UI so an agent can tell a user exactly
where to tap. "API" says whether the public SDK can read or write the value.
Everything marked "app only" cannot be read or changed through the public API.

Settings apply as soon as they are toggled unless the screen has a 儲存 / 變更 /
OK button.

## Bottom tabs

| Tab | Content |
|---|---|
| 首頁 | Robot picker (top, e.g. `Pro-2 ⌄`), 通知 bell, history icon, robot image, "要搬運哪個家具？" furniture cards. Each card's `···` → 編輯家具 / 重設家具位置 / 刪除家具 |
| 地圖 | Current map with destinations, homes and the dock. `目的地列表` chip on top, `+` and `···` menus top right |
| 例行程序 | Routines. Empty state: "沒有例行動作", button 新增例行動作. Triggers: time, voice command, multi-function button |
| 設定 | "Kachaka設定": list of every registered robot, the active one tagged 已選取 |

## 設定 → robot → Kachaka設定

| Row | Shows | Detail screen | API |
|---|---|---|---|
| 名稱 | robot name | — | read: `get_robot_info` |
| Kachaka音量 | inline slider 0–10 | — | read/write: `set_speaker_volume` |
| 行駛時播放音樂 | 關閉 / song | List: 無, uploaded sounds (with `···`), built-in tracks, "新增音樂". Selecting several plays one at random | app only |
| Kachaka 的聲音 | e.g. 日本語（高音） | 日本語（低音） / 日本語（高音） | app only |
| 移動速度 | e.g. 非常快 | Screen "Kachaka速度": slider 較慢 ↔ 較快, plus toggle **以最高速度搬運家具** | app only |
| 移動謹慎度 | e.g. 大膽 | Screen "Kachaka移動謹慎度": slider 謹慎 ↔ 大膽 | app only |
| 預設目的地 | destination name | "選擇預設目的地": the place furniture goes when a voice command names no destination | read: `default_location_id` |
| 安心功能 | — | see below | app only |
| Kachaka API | — | toggle 啟用 Kachaka API, red link 初始化 Playground | app only |
| Fleet Manager 設定 | — | 序號, IP 位址, 通行碼 (未設定) | — |
| 地區 | — | Screen "語言與地區" → 地區 = time zone (e.g. 亞洲/台北) | — |
| 進階設定 | — | see below | mostly app only |
| 活動資料 | total distance | monthly distance chart, carry count (搬運次數), driving time (行駛時間), per-destination carry counts | — |
| 連線模式 | e.g. 本機（手動 IP） | 網際網路連線 / 本機連線（僅 LAN 連線）; under LAN: 自動（mDNS 環境） / 手動輸入 IP + IP field. LAN mode disables schedules, firmware updates, remote support and 拜託 Kachaka | — |
| 軟體自動更新 | 開啟/關閉 | toggle 自動更新, "目前版本為 v3.18.1" | read: `get_robot_version` |

### 安心功能

| Row | Note |
|---|---|
| 突出障礙物偵測 | 3D sensor avoids table tops and other protruding obstacles |
| 僅本體行駛時的偵測高度 | e.g. 30cm |
| 地面障礙物偵測 | camera-based; turn off if floor transitions stop the robot |
| 階差偵測 | step detection |
| 停止時煞車 | keeps the robot from sliding on slopes |

### 進階設定

| Row | Note |
|---|---|
| 暗處模式 | LED stays lit while driving so it can drive in the dark |
| 家具一體化模式 → 搬著家具充電 | |
| 家具一體化模式 → 持續搬著家具 | was greyed out while 搬著家具充電 was off. While on, the robot never puts furniture down (API undock is refused with `10270`/`10271`) |
| 禁止進入地毯 | |
| 「ねぇカチャカ」回應音 | |
| 佇列功能 | with 詳細說明 link |
| 語音辨識 | |
| 充電時 LED 閃爍 | off = blink for 60 s after charging starts, on = blink continuously |
| 警示燈顯示 | green flashing LED while driving (off = steady white) |
| 自動返回充電座 → 未搬著家具時 | e.g. 30秒後. Readable: `get_auto_homing` |
| 自動返回充電座 → 搬著家具時 | e.g. 關閉 |
| 充電座附近自動充電 | off = the robot will not dock by itself even if pushed off the dock or after a power cut |
| 到達目的地所需時間上限 | e.g. 5分30秒 |
| 技術支援 → 允許遠端支援存取 | lets the Kachaka support team connect remotely |

## 地圖 tab menus

`···` menu: 重設所有家具位置, 地圖列表, 目的地列表, 充電座列表, 自我定位校正標記列表,
手動操作Kachaka, 地圖匯出.

`+` menu: 新增地圖, 編輯地圖, 地圖匯入, 新增目的地, 新增充電座, 新增自我定位校正標記,
編輯禁入區域, 編輯可進入區域, 指定行駛速度區域, 坡道區域指定, 環境變化區域指定,
指定行駛路線.

### 目的地列表 → destination `···`

Menu: 將 Kachaka 移動到這裡 (moves the robot right away) / 編輯 / 刪除.

**編輯目的地**:

| Row | Note | API |
|---|---|---|
| 目的地名稱 (with `···`) | free-text names cannot be used in voice commands | read: `name` |
| 家具擺放方式 | one of three modes, see below | partly readable |
| 其他設定 → 以語音指令辨識 | | read: `ignore_voice_recognition` (inverted) |
| 變更目的地位置 / 刪除目的地 | buttons | — |

**家具擺放方式** (how furniture is set down at this destination):

| Mode | Extra settings on the screen | API |
|---|---|---|
| 一般（預設） | "一般時的設定": **略過位置調整** (if exact alignment is hard, accept a small offset and put the furniture down anyway), **略過方向調整** (skip heading adjustment after arriving) | options app only |
| 依照牆面方向擺放家具 | none | read: `undock_aligning_to_wall` |
| 依照牆面標記 | stops aligned to a 10 cm × 10 cm wall marker (link: 標記 PDF 檔與設置方式). **與標記的距離・角度**: 與牆面的距離 (cm, 0.5 steps), 與標記中心的左右距離 (cm, 0.5 steps), 角度 (±10°, 1° steps). Values use a picker wheel, not a text field. **停止時重新調整** is a robot-wide setting, not per destination | app only |

### 指定行駛路線

Two tabs.

**指定行駛線** (designated lines): map preview, button 變更指定行駛線, help link
什麼是指定行駛線？. `···` → **障礙物繞行** sheet:

- 繞過障礙物 (toggle). On = go around obstacles. Off = stop in front of them and
  wait until they clear or a timeout passes.
- When off: "有障礙物時，Kachaka 會 [N] 秒 原地停止" (e.g. 50).

**地點間路線** (routes between destinations): toggle 所有移動都只在地點間路線上行駛（beta）,
a 排序 handle, and one row per route ("從待命點到廁所2", icon → for one-way, ⇄ with
a return route). `+` adds a route.

**編輯地點間路線** (儲存 top right, 取消 top left):

- Tabs 去程 / 回程. 回程 starts as "沒有回程路線。" + link 建立回程路線. Once
  created it has 新增途經點, 改成與去程相同路線 and 刪除回程路線. A return route is
  stored as its own route with start and end swapped.
- Rows: start destination, 途經點 (count), end destination.
- **在直線路徑停止後轉向**: at corners on straight segments, stop and then turn so
  the path does not swing wide. Stored per route.
- **選擇指定行駛線**: map where you tick which designated lines the route may use
  (ticked = green check). OK to confirm.
- 刪除地點間路線.

**變更途經點** (取消 / OK): list of waypoints with coordinates, `+ 新增`, `編輯`.
Waypoint `···` → **變更路徑類型** (screen "到下一個地點的路徑類型": 曲線 (default) / 直線)
and **指定座標**.

## 編輯家具 (首頁 → furniture card `···` → 編輯家具)

| Row | Note | API |
|---|---|---|
| 家具名稱 (with `···`) | | read: `name` |
| 家具稱呼 → 新增稱呼 | names for voice commands | read: `recognizable_names` |
| 家具搬運速度 | screen "Kachaka速度": 2-stop slider 較慢 / 普通 | read: `speed_mode` |
| 平順起步 | toggle: start moving gently while carrying this furniture (heavy or top-heavy loads) | app only — **not** `speed_mode` |
| 家具・載物尺寸 | **家具高度** (13–160 cm; furniture is only put away where there is at least this much clearance) or **貨物三邊尺寸** (width 0–65, depth 0–50, height 0–148 cm; lets the robot pass with overhanging cargo). Row shows "高度 150cm" or "W37 x D32 x H40". Press 變更 to save | height readable via `size` |
| 進階設定 → 以語音指令辨識 | | read: `ignore_voice_recognition` (inverted) |
| 變更收納地點 / 重設家具位置 / 刪除家具 | buttons | — |

## Automating the desktop app (macOS)

The app is Flutter. There is no accessibility tree to query, so drive it with
screenshots and coordinates:

- `screencapture -x -R0,0,W,H` gives a 2× Retina image; scale it down to W×H
  so image pixels equal click points.
- Post real mouse events with CoreGraphics (`CGEventCreateMouseEvent` from JXA).
  Activate the app first, because a click on another window gets lost.
- The first click right after a screen change is sometimes dropped. Take a
  screenshot and click again if nothing changed.
- Picker wheels ignored synthetic scroll and drag events. Tap the value you want in the
  visible wheel instead.
- Type numbers by pasting: set the clipboard with `pbcopy`, then ⌘A ⌘V ⏎.
  Pasting avoids the active input method (e.g. Zhuyin) changing what you type.
- Confirm every change by reading the robot state back, not from the screenshot.
