# GeoTIFF 地理位置数值对照工具

这是一个本机运行的浏览器工具，用于检查两份描述同一区域高程的 GeoTIFF：

- 分别显示坐标参考（CRS）、原生/经纬度范围、尺寸、波段、dtype、单位、scale、offset、nodata。
- 在服务端用 Rasterio/GDAL 做坐标转换、窗口读取和重投影；浏览器只显示服务端返回的当前范围数值网格。
- 两份栅格在 WGS84 视图中地理对齐，可拖动分隔线，也可查看 `B - A` 差值。
- 只有共同覆盖、所选波段单位可确认一致、且当前位置双方都有有效标定时才计算差值；缺测不补零、不推测。
- 单击位置会分别查询两份原始文件的整数行列、原始存储值和标定值，同时把这些读数与当前画面的 bilinear 显示重采样值分开。
- 选点按经纬度保存，缩放、平移、换配色或切换差值后仍代表同一地理地点。
- 重新上传一侧文件会生成新的 `file_id`；旧结果不能与新文件混入同一张差值图。
- 不包含在线底图、行政区搜索、统计看板、文件共享或账号系统。检查文件只保存在当前服务进程的会话目录中。

## 环境

- Python 3.11
- Rasterio 1.4.4（wheel 自带 GDAL 库，不依赖机器上另行安装的 GDAL 命令）
- Node.js 20+（构建 TypeScript/Vite 前端）

## 首次启动

```bash
./scripts/setup.sh
./scripts/run.sh
```

然后打开：

```text
http://127.0.0.1:8000
```

`setup.sh` 会创建项目内 `.venv`、安装 `backend/requirements.txt`、构建前端，并可重新生成样本。

如果系统 Python 缺少 `venv` 模块，可先安装系统包 `python3.11-venv`，或让用户站点中的 `virtualenv` 可用后再运行脚本。

## 可重复生成的小数值样本

样本不是图片，而是两份正常 GeoTIFF，通过页面按钮走普通 `/api/upload` 上传和后续计算：

```bash
python3 scripts/make_samples.py
```

- `samples/sample_a_utm.tif`：EPSG:32632，5 m 像元，Int16，`nodata=-9999`，`scale=0.1`，`offset=50`，单位 m。
- `samples/sample_b_wgs84.tif`：EPSG:4326，约 0.0001° 像元，Float32，NaN/mask nodata，单位 m。
- 有效重叠区的物理模型为：

```text
A = 200 + 4000 * (lon - 10.05) + 3000 * (lat - 49.95)  米
B = A + 2.5                                              米
```

- 两个内部缺测区域只部分重叠，可检查 A-only、B-only、双方缺测和双方有效位置。
- A 中已知点 `lon=10.05205, lat=49.95155`：`row=24, col=32`，原始值 `1628`，标定值 `212.8 m`。
- B 中同一地理位置：`row=14, col=20`，原始/标定值约 `215.35 m`。因此原始读数差约 `2.55 m`；重投影显示网格边缘的 bilinear 值会有少量差异，不能把它当作原始像元读数。

页面中点击“载入样本 A（UTM/scale）”和“载入样本 B（WGS84/缺测）”，选择波段 1，再进入共同覆盖区即可检查。

## API

所有接口都可独立调用，请求头带当前检查会话 ID：

```http
X-Session-ID: <ephemeral-id>
```

- `POST /api/session`：建立新检查会话。
- `GET /api/session`：读取两份文件元数据和共同覆盖/比较条件。
- `POST /api/upload/a`、`POST /api/upload/b`：multipart 字段 `file` 上传 GeoTIFF。成功后返回新文件状态；失败时指明哪一侧不能使用，另一侧保留。
- `POST /api/view`：输入波段、WGS84 范围、当前画布宽高和请求 ID，返回自定义二进制网格。载荷包含元信息、A/B/差值 Float32 数组及每个数组的有效性、单位和统计。
- `POST /api/point`：输入经纬度和双方波段，返回各自 CRS 坐标、行列、原始值、scale/offset 后值、nodata 状态以及原始读数差。

### 视图二进制格式

`/api/view` 的 MIME 是 `application/x-geotiff-check-grid`：

1. magic：`GTCG`
2. little-endian uint32 JSON header 长度
3. UTF-8 JSON header
4. 若 header 中 `arrays.a=true`，跟随 `width * height` 个 little-endian Float32
5. 同样顺序跟 B 和差值数组

NaN 只用于表示该显示位置无有效数据，不参与减法。

## 计算原则

- 输入计算始终使用 GeoTIFF 的地理变换、CRS、mask/nodata、scale 和 offset。
- 显示重采样使用服务端 WGS84 `WarpedVRT` 的 bilinear；有效性 mask 独立用 nearest 生成，避免缺测洞边缘被插值伪装成有效值。
- 原始点查询直接读取对应源文件中整数行列的一个像元，不使用当前显示图片的像素值。
- 差值网格在共同的输出网格上计算 `calibrated B - calibrated A`，双方任一无有效数据即为缺测。
- 颜色拉伸、地形色/灰度只控制观察方式；不会改变视图或点接口的数值。
- 单位不自动换算。两份波段没有可确认的一致单位时，页面和 API 会给出原因，且不生成看似有效的差值图。

## 开发模式

终端 1：

```bash
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

终端 2：

```bash
cd frontend
npm run dev
```

Vite 会把 `/api` 和 `/samples` 代理到 `127.0.0.1:8000`。
