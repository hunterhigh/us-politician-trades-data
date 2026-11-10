# 纸质与扫描件识别（冻结）

本包保存 House、Senate、OGE 纸表、扫描件、OCR 几何、源图像观察及既有纸表纠错的实现。冻结，不再开发新功能、改写识别规则或更改持久化 schema／parser version。

本次迁移只调整相对导入及四处基于 `__file__` 的资源定位；识别、资格、缓存和序列化内容保持不变。现有数据及修正文件保留原位置。根包中的同名短模块是兼容导入，指向同一个实现对象，供冻结工作流和其他线程负责的代码继续使用；不包含另一套实现。

现有调用与历史缓存继续受原有校验约束。迁移验收使用同一份固定 evidence/review 生成候选及完整快照，逐文件逐字节比较；不以新增测试替代此验收。

实现模块：

- `house_asset_line_order.py`
- `house_image_body_routing.py`
- `house_paper_commit.py`
- `house_paper_owner_correction.py`
- `house_paper_owner_inputs.py`
- `house_paper_pipeline.py`
- `house_ptr_amount_header.py`
- `house_ptr_amount_header_preparation.py`
- `house_ptr_cell_observation.py`
- `house_ptr_duplicate_observation.py`
- `house_ptr_fixed_source_core.py`
- `house_ptr_key_field_reread.py`
- `house_ptr_key_fields.py`
- `house_ptr_owner_reading.py`
- `house_ptr_paper_qualification.py`
- `house_ptr_paper_temporal.py`
- `house_ptr_section_context.py`
- `house_ptr_table_region.py`
- `ocr_cache.py`
- `ocr_geometry.py`
- `oge_278t_header_ocr.py`
- `oge_278t_printed_numbers.py`
- `oge_278t_raster_inventory.py`
- `oge_278t_raster_slots.py`
- `oge_278t_source_quad_cells.py`
- `oge_annual_scan_cover.py`
- `oge_annual_scan_dates.py`
- `oge_annual_scan_sections.py`
- `oge_handwritten_date.py`
- `oge_ocr_line_order.py`
- `oge_saved_observations.py`
- `reviewed_house_paper.py`
- `reviewed_senate_paper.py`
- `reviewed_whitehouse_ocr.py`
- `ruled_cell_blank.py`
- `senate_observation_data.py`
- `senate_paper.py`
- `senate_paper_account_scope.py`
- `senate_paper_accounts.py`
- `senate_paper_cell_faces.py`
- `senate_paper_dates.py`
- `senate_paper_marks.py`
- `senate_paper_observation.py`
- `senate_paper_runtime.py`
- `senate_slot_inventory.py`
