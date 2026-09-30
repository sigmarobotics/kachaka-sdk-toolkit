# 影響結果的設定（kachaka_api 3.17.5 實查）

一次驗證跑出來的結果，只有在「同一組組態」下才可比較。同一份程式碼，在
`auto_homing` 開著的機器人上跑，跟在關著的機器人上跑，是兩個不同的實驗。

`KachakaQueries.fingerprint()` 把 API 抓得到的那些設定一次讀下來，回傳
`digest`（fingerprint canonical JSON 的 sha1）。**每筆驗證結果都存 digest**：
digest 不同 ＝ 兩次跑的條件不同，別拿來對照。

抓不到的部分在 [API 抓不到、只能人記](#api-抓不到只能人記) —— 那幾條沒有欄位
可讀，只能在驗證紀錄裡手寫。

> 來源行號皆為本機安裝的 `kachaka-api 3.17.5.0`
> （`.venv/lib/python3.12/site-packages/kachaka_api/base.py`，
> `importlib.metadata.version("kachaka-api")` 查得）。
> **換韌體／換 SDK 版本要重驗這張表**：行號會位移，欄位會增減，
> 3.16.1 之前根本沒有 digest RPC。

## fingerprint 抓得到的欄位

| 欄位 | 為什麼影響 app 行為 | 來源 |
|---|---|---|
| `serial` | 機器人本體識別。同型號不同台的貼標校正、輪徑磨耗、相機安裝角都不一樣，跨台比較數字前先確認是同一台 | `get_robot_serial_number()` base.py:69 → `str`（經 `conn.serial` 永久快取） |
| `fw` | 韌體版本改指令語意。3.18.1 實測 `move_shelf()` 不會放下貨架、`return_shelf()` 空手時會去把貨架接回來（見 `command-semantics-field-3.18.1.md`）；「Readjust When Stopped」在 3.17.5 壞、3.17.8 修好 | `get_robot_version()` base.py:76 → `str`（經 `conn.version` 永久快取） |
| `map_id` | **座標系**。換地圖 ＝ 換座標原點，所有寫死的 `(x, y, theta)` 與 location id 全部失效。跨 map 比對路徑長度或到點誤差沒有意義 | `get_current_map_id()` base.py:639 → `str` |
| `map_name` | 人類可讀的 map 對照（`1F Office` / `2F Lab`）。純粹給讀 log 的人用，不影響行為 | `get_map_list()` base.py:634 → `RepeatedCompositeContainer`（`.id` / `.name`），用 `map_id` 比對取出 |
| `locations_digest` | 目的地清單本身。少一個目的地、改名、改 `LocationType`（例如 SLAM marker 變一般目的地）都會讓「移動到 X」的行為改變或直接失敗 | sha1 over `stub.GetLocationsDigest(pb2.GetRequest())` → `GetLocationsDigestResponse{metadata, locations: LocationDigest(id, name, type)}`。**base.py 沒有包這支 RPC**（`grep -i digest base.py` 無結果），要走 `sdk.stub` |
| `shelves_digest` | 貨架清單。貨架被刪掉或改名，`dock_shelf("Shelf A")` 的名稱解析就會找不到 | sha1 over `stub.GetShelvesDigest(pb2.GetRequest())` → `GetShelvesDigestResponse{metadata, shelves: ShelfDigest(id, name)}`。同樣不在 base.py |
| `auto_homing` | 開著的話，**閒置的機器人會自己回充電座**。驗證中途的等待、拍照、量測，只要停太久機器人就跑走了，結果看起來像「指令沒執行」 | `get_auto_homing_enabled()` base.py:590 → `bool` |
| `manual_control` | 開著的時候是手動速度模式，**任務指令會被拒絕**。跑完 `set_velocity` 忘記關，下一輪驗證整批指令失敗 | `get_manual_control_enabled()` base.py:604 → `bool` |
| `speaker_volume` | 0–10。語音類 FEAT（到站播報、搖晃確認的提示音、TTS 引導）在音量 0 時「功能正常但現場聽不到」——驗證會判 pass，現場會判 fail | `get_speaker_volume()` base.py:736 → `int` |
| `location_flags[<name>].undock_shelf_aligning_to_wall` | 放架時是否把貨架靠牆貼平。決定最終停位，影響放架精度與後續 dock 是否對得上 | `pb2.Location` 欄位 5（`Location.DESCRIPTOR.fields` 查得）；讀自 raw `get_locations()` base.py:552 |
| `location_flags[<name>].undock_shelf_avoiding_obstacles` | 放架時是否避開周邊障礙。開著會讓實際落點偏離登錄座標 | `pb2.Location` 欄位 6 |
| `location_flags[<name>].ignore_voice_recognition` | 這個目的地是否被語音喚醒忽略。語音派工的 FEAT 在這裡被關掉時，「講了沒反應」不是辨識問題 | `pb2.Location` 欄位 7 |
| `shelf_flags[<name>].speed_mode` | 貨架層級的搬運速度。`SHELF_SPEED_MODE_LOW` 是刻意慢慢搬（載重不穩），**會拉長每趟時間**——量 throughput、比對到點時間時是變因。存的是 enum 名不是數字，紀錄才讀得懂 | `pb2.Shelf` 欄位 9，enum `pb2.ShelfSpeedMode`（`Shelf.DESCRIPTOR.fields` / `ShelfSpeedMode.DESCRIPTOR.values` 查得：`_UNSPECIFIED` 0 / `_LOW` 1 / `_NORMAL` 2）；讀自 raw `get_shelves()` base.py:564 |
| `shelf_flags[<name>].ignore_voice_recognition` | 貨架層級的語音忽略。語音叫貨架沒反應時，先看這個再懷疑辨識 | `pb2.Shelf` 欄位 10（bool）；同樣讀自 raw `get_shelves()` base.py:564 |

### 為什麼 `location_flags` 走 raw `get_locations()`

`KachakaQueries.list_locations()` 的包裝會**丟掉 `ignore_voice_recognition`**，
並把另外兩個 flag 改名成 `undock_aligning_to_wall` /
`undock_avoiding_obstacles`。fingerprint 要的是 proto 原名與完整欄位，所以
直接讀 `self.sdk.get_locations()`，不經那層包裝。

### `shelves_digest` 的盲點——已由 `shelf_flags` 覆蓋

`shelves_digest` 只雜湊 `ShelfDigest` 的 `id` / `name`。所以把一台貨架從
`SHELF_SPEED_MODE_NORMAL` 切成 `_LOW`，id 和名字都沒變，**`shelves_digest`
一個字都不會動**——但那台貨架的每一趟都變慢了，拿來跟切換前的搬運時間對照
就是錯的實驗。`ignore_voice_recognition`（貨架層級）同理。

這兩欄現在由 `shelf_flags` 蓋掉（走 raw `get_shelves()` base.py:564，不走
digest RPC），所以**整包 `digest` 會動**，不必再手記 `list_shelves()` 的輸出。
兩者的角色不重疊，`shelves_digest` 保留：它抓貨架清單本身的增刪改名，
`shelf_flags` 抓每台貨架的兩個行為開關。

> 這是 digest RPC 這類「輕量摘要」的通則：摘要欄位少，**沒進摘要的設定改了
> 摘要不會動**。之後 fingerprint 想再擴，判準是「這個欄位改了會不會讓兩次跑
> 不可比」，不是「digest RPC 有沒有給」。

## API 抓不到、只能人記

以下沒有任何 RPC 可讀。**驗證紀錄要人工手寫**，不然日後重跑對不上。

| 設定 | 為什麼影響結果 | 來源／查證 |
|---|---|---|
| **韌體端安全／避障設定**（避障靈敏度、安全距離、減速區） | 決定機器人遇到人／障礙時是繞、是停、還是等。同一條路線在寬鬆設定下順跑、在嚴格設定下每次都停下來等 | base.py 全檔 `grep -niE "safety\|obstacle\|avoid\|collision\|speed_limit\|max_velocity"` **零命中**；pb2 也沒有對應 message。只能在 app 設定，只能手記 |
| **docking 布林**（此刻有沒有在充電座上） | 充電座上很多操作行為不同：ToF 相機／`camera_info` 在座上取不到，離座要先 `depart_from_charger` | 沒有 `is_docked` RPC。只能由 `get_battery_info()` base.py:93 → `(float, pb2.PowerSupplyStatus)` 的第二個值**推論**：`POWER_SUPPLY_STATUS_CHARGING` ⇒ 在座上。其餘四值（`UNSPECIFIED` / `DISCHARGING` / `NOT_CHARGING` / `FULL`）分不出「離座」與「在座但充飽／不充」，所以這是推論不是讀值 |
| **速度上限** | 決定實際行駛速度，直接影響到點時間。這個上限**不是機器人回報的**，是 client 端算出來的——所以看 fingerprint 看不到，改了 toolkit 就改了 | SDK 常數 `MAX_LINEAR_VELOCITY = 0.3` base.py:27、`MAX_ANGULAR_VELOCITY = 1.57` base.py:28，`_impl_set_robot_velocity` base.py:615–616 拿它們做正規化；toolkit 另外自己 clamp：`commands.py:767-768`（`linear` 夾 ±0.3、`angular` 夾 ±1.57）、`commands.py:259`（`signed_velocity` 夾 ±0.3）。沒有 Get RPC |
| **app 端「保持握持家具」／「握持家具時充電」** | 直接讓 `return_shelf()` / `undock_shelf()` 被拒（`10270` / `10271` / `11019` / `11501`），而且設定開著時**完全沒有 API 路徑可以把貨架放下** | SKILL.md「App settings that break the API invisibly」已列；只能靠 error code 反推，沒有欄位可讀 |

app 每一頁的 zh-TW 標籤與哪些值是 app-only，對照
[`app-ui-screens.md`](app-ui-screens.md)。

## 用法

```python
from kachaka_core import KachakaConnection, KachakaQueries

fp = KachakaQueries(KachakaConnection.get(robot_ip)).fingerprint()
# {"ok": True, "fingerprint": {...}, "digest": "3f2a…", "partial": []}
```

單一 RPC 失敗不會整包掛掉：該欄位填 `None`，欄位名進 `partial`，digest 照樣算
得出來。所以兩次都在同樣欄位失敗、其餘相同的讀取仍然比得出相等——但
`partial` 非空時，記錄上要一起存，不然「digest 相同」會被誤讀成「組態完整相同」。
