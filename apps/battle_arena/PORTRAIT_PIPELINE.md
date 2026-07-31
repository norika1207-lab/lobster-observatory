# Portrait Generation Pipeline

## 概覽

三個核心邏輯串聯：

```
Pollinations AI → [臉朝左硬判斷 + 自動翻轉] → [Trap+Hex 遮罩] → 存檔
```

---

## 1. 生成邏輯（Pollinations AI）

### 端點

```
GET https://image.pollinations.ai/prompt/{encoded_prompt}?width=1408&height=1408&nologo=true&model=flux&seed={seed}
```

- 免費、無配額、無需 API key
- `model=flux` 畫質最佳，適合肖像
- `seed` 固定可重現，換 seed 產生不同結果

### Prompt 寫法

```python
prompt = (
    f"low poly geometric portrait, {desc}, "
    "face turned to the LEFT showing left side profile, "
    "eye visible on the LEFT side of image, nose points LEFT, "
    "bare head NO helmet NO hat, face and neck only, dark navy background, "
    "angular polygon facets on skin, dramatic shadows, vector art style, "
    "sharp geometric triangles, professional digital illustration"
)
```

**關鍵原則：**
- **不要** 提運動名稱（MotoGP/F1/Boxing），會觸發頭盔/裝備
- `{desc}` 只描述人的外貌特徵（種族、髮色、臉型）
- 明確要求 `face turned to the LEFT`、`eye visible on the LEFT side`
- 明確排除 `NO helmet NO hat`
- 風格要求：`low poly geometric`、`angular polygon facets`、`dark navy background`

### 後處理

```python
img = Image.open(BytesIO(resp.content)).convert('RGB')
img = ImageEnhance.Brightness(img).enhance(1.10)  # 提亮 10%
```

### 重試策略

```python
for attempt in range(5):
    actual_seed = seed + attempt * 7919  # 質數偏移，確保每次種子不同
    # ... 生成 + 方向檢查
    # 若仍朝右：continue 重試
```

---

## 2. 臉部朝向硬邏輯

### 原理：輪廓邊緣法（Silhouette Edge Detection）

側臉肖像的物理特徵：
- **朝左**：鼻子突出於左側 → 輪廓最左點在影像左方 → median leftmost x **小**
- **朝右**：鼻子突出於右側 → 輪廓最左點在影像右方 → median leftmost x **大**

### 演算法

```python
def face_direction_ratio(img: Image.Image) -> float | None:
    # 1. 灰階 + 縮放 + 模糊（去雜訊）
    gray = img.convert('L').resize((256, 256), Image.LANCZOS)
    gray = gray.filter(ImageFilter.GaussianBlur(1))
    arr = np.array(gray, dtype=np.float32)
    h, w = arr.shape

    # 2. 只看鼻/嘴/下巴區域（35%~65% 高度）
    zone = arr[int(h * 0.35):int(h * 0.65), :]

    # 3. 自適應閾值：非黑像素的第 40 百分位
    non_black = zone[zone > 20]
    if len(non_black) < 100:
        return None
    threshold = float(np.percentile(non_black, 40))

    # 4. 每列找最左亮像素
    leftmost_xs = []
    for row in zone:
        bright_cols = np.where(row > threshold)[0]
        if len(bright_cols) > 0:
            leftmost_xs.append(bright_cols[0])

    if len(leftmost_xs) < 5:
        return None

    # 5. 用 median 抗雜訊，除以寬度正規化
    return float(np.median(leftmost_xs)) / w
```

### 判斷規則

| ratio 值 | 判斷 |
|----------|------|
| `< 0.35` | 朝左 ✓ |
| `> 0.45` | 朝右 → 需翻轉 |
| `0.35–0.45` | 不確定（保留原樣） |

### 自動修正

```python
def ensure_facing_left(img: Image.Image) -> Image.Image:
    ratio = face_direction_ratio(img)
    if ratio is not None and ratio > 0.45:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img
```

### 整合進生成流程

```python
img = generate_from_pollinations(prompt, seed)
img = ImageEnhance.Brightness(img).enhance(1.10)

ratio = face_direction_ratio(img)
if ratio is not None and ratio > 0.45:
    print(f"  facing RIGHT ({ratio:.3f}), flipping...")
    img = img.transpose(Image.FLIP_LEFT_RIGHT)

# 翻轉後再驗一次
final_ratio = face_direction_ratio(img)
if final_ratio is not None and final_ratio > 0.45:
    # 翻了還是朝右（edge case），換 seed 重試
    continue
```

---

## 3. Trap+Hex 遮罩邏輯

### 目的

在肖像右側疊加深色梯形 + 六角格紋，製造「卡牌邊框」視覺效果。

```
┌──────────────────────────────────┐
│                  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓│  ← 梯形遮罩（Trap）
│   FACE           ▓HEX HEX HEX▓▓▓│
│   SIDE           ▓▓▓HEX HEX▓▓▓▓▓│  ← 六角格紋（Hex Grid）疊在梯形上
│   PROFILE        ▓▓▓▓▓HEX▓▓▓▓▓▓▓│
│                  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓│
└──────────────────────────────────┘
```

### 參數

```python
TRAP_ALPHA = 202   # 梯形黑色不透明度（0-255），越大越暗
HEX_A = 245        # 六角外框透明度
HEX_B = 187        # 六角三角形 1 透明度
HEX_C = 158        # 六角三角形 2 透明度
```

### 梯形位置自動偵測

```python
def apply_mask(img_rgb):
    img = img_rgb.resize((1408, 1408), Image.LANCZOS).convert('RGBA')
    w, h = img.size

    # 從右向左掃描中間列，找到臉的最右邊
    ear = 0.72  # 預設耳朵位置（圖寬 72%）
    my = h // 2
    for x in range(w-1, 0, -1):
        r, g, b, *_ = px[x, my]
        if r > 60 or g > 60 or b > 60:
            ear = x / w
            break

    # 梯形頂邊（top-left）= 耳朵位置 +2%，最多 88%
    tl = min(round(ear + 0.02, 2), 0.88)
    # 梯形底邊（bottom-left）= 頂邊 -40%（右傾斜）
    bl = round(tl - 0.40, 2)

    trap = [
        (int(w * tl), int(h * 0.02)),   # 左上
        (int(w * 0.93), int(h * 0.02)), # 右上
        (int(w * 0.93), int(h * 0.98)), # 右下
        (int(w * bl), int(h * 0.98)),   # 左下（較左，形成斜邊）
    ]
```

### 六角格紋

```python
hr = 28           # 六角半徑（px）
sx = hr * √3      # 水平間距
sy = hr * 1.5     # 垂直間距

for row in range(-1, int(h/sy)+3):
    for col in range(-1, int(w/sx)+3):
        cx = col*sx + (sx*0.5 if row%2 else 0)  # 奇偶列錯位
        cy = row * sy
        if in_poly(cx, cy, trap):               # 只在梯形內畫
            pts = hex_pts(cx, cy, hr-2)
            d.polygon(pts, outline=(55,55,55, HEX_A))
            d.polygon([pts[0],pts[2],pts[4]], outline=(46,46,46, HEX_B))
            d.polygon([pts[1],pts[3],pts[5]], outline=(46,46,46, HEX_C))
```

### 完整函式

```python
import math, numpy as np
from PIL import Image, ImageDraw

TRAP_ALPHA = 202
HEX_A, HEX_B, HEX_C = 245, 187, 158

def hex_pts(cx, cy, r):
    return [(cx + r*math.cos(math.radians(60*i-30)),
             cy + r*math.sin(math.radians(60*i-30))) for i in range(6)]

def in_poly(px, py, poly):
    ins = False
    x1, y1 = poly[-1]
    for x2, y2 in poly:
        if ((y1 > py) != (y2 > py)) and (px < (x2-x1)*(py-y1)/(y2-y1)+x1):
            ins = not ins
        x1, y1 = x2, y2
    return ins

def apply_mask(img_rgb):
    img = img_rgb.resize((1408, 1408), Image.LANCZOS).convert('RGBA')
    w, h = img.size
    px = img.load()
    ear = 0.72; my = h // 2
    for x in range(w-1, 0, -1):
        r, g, b, *_ = px[x, my]
        if r > 60 or g > 60 or b > 60:
            ear = x / w; break
    tl = min(round(ear + 0.02, 2), 0.88)
    bl = round(tl - 0.40, 2)
    layer = Image.new('RGBA', (w, h), (0,0,0,0))
    d = ImageDraw.Draw(layer)
    trap = [(int(w*tl),int(h*0.02)),(int(w*0.93),int(h*0.02)),
            (int(w*0.93),int(h*0.98)),(int(w*bl),int(h*0.98))]
    d.polygon(trap, fill=(0,0,0, TRAP_ALPHA))
    hr = 28; sx = hr*math.sqrt(3); sy = hr*1.5
    for row in range(-1, int(h/sy)+3):
        for col in range(-1, int(w/sx)+3):
            cx = col*sx + (sx*0.5 if row%2 else 0); cy = row*sy
            if in_poly(cx, cy, trap):
                pts = hex_pts(cx, cy, hr-2)
                d.polygon(pts, outline=(55,55,55, HEX_A))
                d.polygon([pts[0],pts[2],pts[4]], outline=(46,46,46, HEX_B))
                d.polygon([pts[1],pts[3],pts[5]], outline=(46,46,46, HEX_C))
    return Image.alpha_composite(img, layer).convert('RGB')
```

---

## 4. 完整 Pipeline 呼叫範例

```python
from gen_portrait import generate_portrait, save_portrait

# 一個選手
img = generate_portrait(
    desc="young Italian man, sharp jaw, light stubble",
    seed=42001,
    max_attempts=5
)
if img:
    save_portrait(img, code="enea",
                  out_dir="/opt/battle_arena/static/cards/motogp",
                  sync_dir="/opt/racing/static/cards/motogp")
```

執行流程：
1. 向 Pollinations 請求圖片（最多 5 次）
2. 每次生成後：`face_direction_ratio()` 判斷方向
3. 若 `> 0.45`（朝右）→ `FLIP_LEFT_RIGHT`
4. 翻轉後再驗一次，若仍朝右 → 換 seed 重試
5. 最終圖儲存為 `{code}_base.png`（原始）
6. `apply_mask()` 疊加梯形+六角遮罩 → 儲存為 `{code}.png`
7. `sync_dir` 有指定時複製到 Racing 靜態目錄

---

## 5. 運動別 Prompt 策略

| 運動 | 描述重點 | 特殊處理 |
|------|----------|----------|
| MotoGP / F1 / NASCAR | 人臉外貌，不提運動 | 寫 `bare head NO helmet` |
| Boxing / UFC / ONE | 人臉外貌 | 同上，`NO mouthguard` |
| Sumo | 大臉、圓潤、日式 | 不提頭盔 |
| Esports | 年輕玩家外貌 | 加 `wearing gaming headset` 或 `cap pulled low` |
| Horse | 馬的外貌（種類、顏色） | 不用人像邏輯，直接圖片 |

---

## 6. 工具腳本位置

| 腳本 | 用途 |
|------|------|
| `/tmp/gen_portrait.py` | 主生成腳本（含硬邏輯） |
| `/tmp/face_direction.py` | 方向偵測模組（獨立可用） |
| `/tmp/fix_orientation_v2.py` | 批次修正現有圖庫方向 |
| `/tmp/rewash_v2.py` | 批次重新套用遮罩 |
