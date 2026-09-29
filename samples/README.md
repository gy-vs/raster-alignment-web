# Small known-value samples

Run `python3 scripts/make_samples.py` from the project root to regenerate these files.

- `sample_a_utm.tif`: EPSG:32632, 5 m cells, Int16, nodata=-9999, scale=0.1, offset=50.
- `sample_b_wgs84.tif`: EPSG:4326, about 0.0001° cells, Float32, NaN/mask nodata.
- The physical model is `z_a=200+4000*(lon-10.05)+3000*(lat-49.95)` metres and `z_b=z_a+2.5` metres where both rasters are valid.
- Interior no-data regions intentionally overlap only partly.
- Load each file in the browser with the sample buttons; it goes through `/api/upload` and the normal view/point calculations.

Generation metadata: A={'path': 'samples/sample_a_utm.tif', 'width': 70, 'height': 55, 'crs': 'EPSG:32632', 'anchor_x': 575472.3661932615, 'anchor_y': 5533774.277108519, 'checks': [{'side': 'A', 'row': 24, 'col': 32, 'lon': 10.05205, 'lat': 49.95155, 'raw_a': 1628, 'value_a': 212.84999999997893, 'scale': 0.1, 'offset': 50.0}]}
B={'path': 'samples/sample_b_wgs84.tif', 'width': 40, 'height': 30, 'crs': 'EPSG:4326', 'checks': [{'side': 'B', 'row': 15, 'col': 20, 'lon': 10.052050000000001, 'lat': 49.95145, 'value_b': 215.0500030517578}, {'side': 'B', 'row': 10, 'col': 10, 'lon': 10.05105, 'lat': 49.951950000000004, 'value_b': 212.5500030517578}, {'side': 'B', 'row': 20, 'col': 25, 'lon': 10.05255, 'lat': 49.950950000000006, 'value_b': 215.5500030517578}]}
