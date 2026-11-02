PY ?= python

.PHONY: help install ingest context verify verify-reuse test reset usage-record

help:
	@echo "install      安装依赖（pyseekdb + pytest）"
	@echo "ingest       把规格/代码/测试导入 seekdb"
	@echo "context      生成开发上下文（Markdown）"
	@echo "verify       六项验收全流程（会调用 MiniMax）"
	@echo "verify-reuse 复用 patches/after 跑验收（不调用模型）"
	@echo "test         跑业务工程测试"
	@echo "reset        恢复修改前基线并清空本地库"
	@echo "usage-record 由 records/ 重新生成技术使用记录"

install:
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install pytest

ingest:
	$(PY) -m sddctx ingest

context:
	$(PY) -m sddctx context

verify:
	$(PY) -m sddctx verify

verify-reuse:
	$(PY) -m sddctx verify --reuse-patch

test:
	$(PY) -m pytest business/tests -q

reset:
	$(PY) -m sddctx restore
	rm -rf data

usage-record:
	$(PY) scripts/build_usage_record.py
