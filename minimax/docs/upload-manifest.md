# 上传清单（网页上传用）

> 由 `scripts/build_manifest.py` 生成。共 **57 个文件**，4306 行，164661 字节。
> 上传完成后运行 `python scripts/check_remote.py` 逐文件校验，它用 git blob SHA 比对，不依赖行数是否看得准。

| # | 文件（相对 `minimax/`） | 行数 | 字节 | blob SHA |
| ---: | --- | ---: | ---: | --- |
| 1 | `.env.example` | 28 | 1009 | `32164177e30a` |
| 2 | `.gitignore` | 6 | 84 | `bcdcb7eea20a` |
| 3 | `.gitkeep` | 0 | 0 | `e69de29bb2d1` |
| 4 | `business/pytest.ini` | 2 | 27 | `5ee647716572` |
| 5 | `business/specs/error-handling-spec.md` | 25 | 1240 | `1f53dd452479` |
| 6 | `business/specs/order-query-spec.md` | 71 | 3438 | `4dc838d87ca8` |
| 7 | `business/src/order_service/__init__.py` | 18 | 463 | `a206c0b2a90e` |
| 8 | `business/src/order_service/errors.py` | 43 | 1661 | `521936e4da5a` |
| 9 | `business/src/order_service/models.py` | 25 | 610 | `d12d853d004e` |
| 10 | `business/src/order_service/repository.py` | 22 | 664 | `0e2855707724` |
| 11 | `business/src/order_service/service.py` | 38 | 1372 | `234c86043c38` |
| 12 | `business/tests/conftest.py` | 51 | 1501 | `c42f666b8641` |
| 13 | `business/tests/test_order_amount_filter.py` | 96 | 3797 | `d9340b7da512` |
| 14 | `business/tests/test_order_query_regression.py` | 75 | 2669 | `989c2a5e86ce` |
| 15 | `docs/submission.md` | 108 | 5412 | `530324552d3d` |
| 16 | `docs/tech-note.md` | 61 | 3545 | `5b9b2ee02191` |
| 17 | `docs/usage-record.md` | 24 | 871 | `4a1ff2a0a298` |
| 18 | `Makefile` | 39 | 964 | `3cf53d10d152` |
| 19 | `patches/before/specs/error-handling-spec.md` | 25 | 1240 | `1f53dd452479` |
| 20 | `patches/before/specs/order-query-spec.md` | 71 | 3367 | `fbf0a2bc35df` |
| 21 | `patches/before/src/order_service/__init__.py` | 18 | 463 | `a206c0b2a90e` |
| 22 | `patches/before/src/order_service/errors.py` | 43 | 1661 | `521936e4da5a` |
| 23 | `patches/before/src/order_service/models.py` | 25 | 610 | `d12d853d004e` |
| 24 | `patches/before/src/order_service/repository.py` | 22 | 664 | `0e2855707724` |
| 25 | `patches/before/src/order_service/service.py` | 38 | 1372 | `234c86043c38` |
| 26 | `patches/before/tests/conftest.py` | 51 | 1501 | `c42f666b8641` |
| 27 | `patches/before/tests/test_order_amount_filter.py` | 96 | 3797 | `d9340b7da512` |
| 28 | `patches/before/tests/test_order_query_regression.py` | 75 | 2669 | `989c2a5e86ce` |
| 29 | `pyproject.toml` | 24 | 537 | `4c710289ae0e` |
| 30 | `README.md` | 189 | 9651 | `08e1a267f288` |
| 31 | `records/context/.gitkeep` | 0 | 0 | `e69de29bb2d1` |
| 32 | `records/db/.gitkeep` | 0 | 0 | `e69de29bb2d1` |
| 33 | `records/minimax/.gitkeep` | 0 | 0 | `e69de29bb2d1` |
| 34 | `records/state/.gitkeep` | 0 | 0 | `e69de29bb2d1` |
| 35 | `records/tests/.gitkeep` | 0 | 0 | `e69de29bb2d1` |
| 36 | `records/verification/.gitkeep` | 0 | 0 | `e69de29bb2d1` |
| 37 | `requirements.txt` | 8 | 260 | `8935cb250bf1` |
| 38 | `samples/verification/README.md` | 73 | 3462 | `f081006ad2b6` |
| 39 | `samples/verification/task.json` | 40 | 1193 | `92db8dad4c40` |
| 40 | `scripts/build_manifest.py` | 55 | 2005 | `0be12452435f` |
| 41 | `scripts/build_usage_record.py` | 166 | 7189 | `a10176b765fb` |
| 42 | `scripts/check_remote.py` | 109 | 4262 | `88e552312979` |
| 43 | `scripts/push_via_api.py` | 129 | 5054 | `56eeb24c5662` |
| 44 | `scripts/run_verification.py` | 22 | 635 | `2abaa896b728` |
| 45 | `sddctx/__init__.py` | 10 | 357 | `f4cf4ad0c377` |
| 46 | `sddctx/__main__.py` | 10 | 155 | `f748d48b2e8f` |
| 47 | `sddctx/builder.py` | 294 | 10727 | `aee6f39ec59c` |
| 48 | `sddctx/cli.py` | 202 | 6828 | `4afa77ca7a0c` |
| 49 | `sddctx/config.py` | 82 | 3051 | `f4129dd50651` |
| 50 | `sddctx/corpus.py` | 245 | 8178 | `02adfa2a24ff` |
| 51 | `sddctx/embedding.py` | 74 | 2458 | `57b9dd272f41` |
| 52 | `sddctx/minimax.py` | 235 | 8044 | `210a23f6424f` |
| 53 | `sddctx/pipeline.py` | 260 | 8587 | `b7268c5e67dc` |
| 54 | `sddctx/powercontext.py` | 192 | 8189 | `4884d8847e4e` |
| 55 | `sddctx/runner.py` | 93 | 2530 | `eae0c7924e5a` |
| 56 | `sddctx/store.py` | 284 | 10478 | `6248e2877469` |
| 57 | `sddctx/verify.py` | 314 | 14160 | `e2c317fadc17` |
