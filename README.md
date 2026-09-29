# 双栅格地理对照工具（GeoTIFF compare）

在浏览器里检查**同一块区域、两份不同 GeoTIFF 高程栅格**在**真实地理位置**上的数值关系。
一份可以是经纬度、另一份是投影坐标、像元大小和存储标定也可以不同——工具先在
坐标参考层面把它们对齐，再谈数值。

这不是“把两个 GeoTIFF 转成 PNG 并排看颜色”，也不是地图查看器：

- 没有在线底图、行政区检索、统计看板；检查对象只有你上传的栅格。
- 浏览器上的每个数字结果都由后端用 Rasterio（自带 GDAL，不调用系统 `gdal` 命令）计算。
- 显示颜色只是观察数值的方式，**不参与任何计算**。
- 只在当前本地检查会话内工作，没有账号、文件共享或后台。

## 能力

- 打开两份文件后显示各自的**坐标参考、范围（原 CRS 与 WGS84）、尺寸、像元大小、
  波段、数据类型、scale/offset、无数据标记、数值单位**。
- 分别选择参与比较的波段；进入**共同覆盖区**，拖动/缩放时两份影像在同一
  Web 墨卡托网格上保持地理位置对齐。
- **分隔线对照**：左右分别显示 A、B，可拖动分隔线查看同一地点的两次测量。
- **差值 A − B**：仅在两份波段数值上可比较时才生成；不可比较时给出原因，
  **不会**产出一张看似有效的差值图。
- GeoTIFF 的 `scale`、`offset`、无数据标记都会影响比较：
  比较在“标定后值”（`raw × scale + offset`）上进行；缺测不做任何推测补全。
- 单位不同但可换算（如米/英尺）时，先统一换算再相减并说明换算系数；
  角度等非长度量、或无法识别单位时会被明确拦截或警告。
- 单击选点：同时给出该地理点在两份**原始文件**中的行/列、像元内位置、
  **原始存储值**、scale/offset 与**标定后值**，以及该点是否两份都有可比较数据。
  选点以经纬度为身份，平移、缩放、换波段、换配色后仍是同一个地理地点。
- 重采样的显示网格与源像元读数在界面上明确区分，互不混用。
- 重新上传某一份会得到新的文件版本标识；旧视图不会被当作新文件结果，
  但已有的选点保留地理含义并对新文件重新读数。
- 几百兆文件只按当前视图窗口读取（WarpedVRT + 概览金字塔 + 窗口化重投影），
  拖动时去抖，不把整份栅格反复发给浏览器。
- 读取出错时指明是哪一份不能用，已经能打开的那份仍可单独查看。

## 目录

```
backend/app/        FastAPI + Rasterio 后端
  rasterio_ops.py   元数据 / scale-offset / 窗口视图 / 选点源像元读数
  compare.py        可比较性判定与差值（含单位换算、双方缺测交集）
  geo.py            CRS、范围与几何重投影
  session.py        仅内存的会话与 a/b 槽位（文件版本标识）
  protocol.py       紧凑视图二进制编码（JSON 头 + float32/uint8，zlib）
  main.py           HTTP 接口，并在生产模式托管前端静态文件
frontend/           TypeScript + Vite，原生 Canvas，无任何地图库
scripts/
  generate_samples.py  生成可复现的小型数值样本（含解析真值）
  backend_test.py      走真实上传接口的端到端检查
samples/            生成出来的样本与 samples_manifest.json
```

## 本机运行

要求：Python 3.11、Node 18+（仅构建前端时需要）。

```bash
./run.sh --rebuild
# 打开 http://127.0.0.1:8000
```

脚本会：建立 `.venv`、安装 `requirements.txt`（rasterio wheel 自带 GDAL）、
`npm install && npm run build`、必要时生成样本，然后单端口启动。

开发模式（前端热更新）：

```bash
# 终端 1
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --port 8000
# 终端 2
cd frontend && npm install && npm run dev   # http://127.0.0.1:5173 ，/api 代理到 8000
```

### 重新生成样本 / 运行后端检查

```bash
.venv/bin/python scripts/generate_samples.py
.venv/bin/python scripts/backend_test.py
```

## 样本与核对方法

`scripts/generate_samples.py` 生成两份描述同一块解析地形的文件（可重复生成）：

- `sample_a.tif`：EPSG:32632（UTM 32N），30 m 像元，**Int16**，
  `scale=0.1`（存储值 ×10），`nodata=-32768`，一个圆形缺测洞。
- `sample_b.tif`：EPSG:32633（UTM 33N），25 m 像元，**Float32/NaN**，
  两个波段（米、英尺），一个位置不同的方形缺测洞。

真值是解析式，写在 `samples/samples_manifest.json`，含三个核对点：
双方有效、A 缺测、B 缺测。建议的检查流程：

1. 分别上传 A、B，确认显示的 CRS/范围/波段/scale/无数据标记与上面一致。
2. 进入共同覆盖区，用分隔线对照同一地点；切到“差值 A − B”，
   缺测洞处应为透明/底纹，差值只出现在双方都有数据处。
3. 单击三个核对点，比较行/列、原始值（A 是 ×10 的整数）、标定后值与
   manifest 的解析真值；A 洞/B 洞点应明确显示“此点不可比较”。
4. 把 B 切到英尺波段：应出现单位换算提示，差值仍以米计、量级不变。

注意：同一地理点在两份文件中落在**各自不同的行列像元**，像元中心最多相差
约一个像元，所以“点差值”与连续解析真值会有与地形梯度、像元尺寸相称的
栅格化差异；界面展示的正是各自源像元的真实读数，而不是把显示像素当成读数。

## 独立 HTTP 接口

均为 JSON，除视图接口返回自定义二进制（`application/x-geocmp-view`）：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/sessions` | 新建仅内存会话 |
| GET | `/api/sessions/{id}` | 当前两槽位元数据与可比较性 |
| POST | `/api/sessions/{id}/slots/{a\|b}/upload` | 流式上传并打开一份 GeoTIFF |
| DELETE | `/api/sessions/{id}/slots/{a\|b}` | 移除某一份 |
| PUT | `/api/sessions/{id}/slots/{a\|b}/band` | 选择参与比较的波段 |
| GET | `/api/sessions/{id}/comparison` | 可比较性判定、原因、共同覆盖区 |
| POST | `/api/sessions/{id}/view` | 当前 Web 墨卡托范围的对齐视图/差值 |
| GET | `/api/sessions/{id}/point?lon=&lat=` | 该地理点两份源文件的行列与原始/标定值 |

视图请求带 `expected_ids`；当槽位文件已被替换、客户端仍请求旧版本时返回
`409 dataset_version_mismatch`，从机制上杜绝新旧结果混入同一幅图。

## 数值语义

- 原始值（raw）：文件里实际存储的数字。
- 标定值（calibrated）：`raw × scale + offset`；无数据标记除外。
- 显示网格：把**原始值**重投影/重采样到当前 Web 墨卡托网格后再标定，仅供观察。
- 差值网格：两份标定值重采样到**同一目标网格**，只在双方都有效的像元上取
  A − B；单方覆盖或缺测为 NaN（界面以底纹/颜色标识），不插值、不外推。
- 点读数：始终回到源文件读取该点所在行列，独立于显示网格。
