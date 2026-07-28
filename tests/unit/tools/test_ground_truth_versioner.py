"""
真值集版本管理器测试（阶段二-2.3）

覆盖:
- 保存版本（首次/递增/原子写入）
- 列出版本（倒序/空数据集）
- 获取指定版本（快照/当前版本）
- 版本对比（新增/删除/修改记录）
- 版本回滚（保留回滚痕迹）
- 变更日志（完整性/原子写入）
- 戒律验证（M1/M2/M4/P4）
"""
import os
import json

import pytest

from tools.ground_truth_builder import GroundTruthDataset, GroundTruthRecord
from tools.ground_truth_versioner import GroundTruthVersioner


# ============================================================
# 夹具
# ============================================================
@pytest.fixture()
def versioner(tmp_path):
    """临时版本管理器"""
    return GroundTruthVersioner(base_dir=str(tmp_path))


@pytest.fixture()
def sample_dataset():
    """样本数据集"""
    ds = GroundTruthDataset(name="test_ds", description="测试数据集")
    ds.add_record(GroundTruthRecord("T1", is_suspicious=True, suspicious_reasons=["大额交易"]))
    ds.add_record(GroundTruthRecord("T2", is_suspicious=False))
    return ds


# ============================================================
# 保存版本测试
# ============================================================
@pytest.mark.unit
def test_save_first_version_returns_one(versioner, sample_dataset):
    """首次保存应返回版本1"""
    version = versioner.save_version(sample_dataset, description="初始版本")
    assert version == 1


@pytest.mark.unit
def test_save_second_version_returns_two(versioner, sample_dataset):
    """二次保存应返回版本2"""
    versioner.save_version(sample_dataset, description="v1")
    # 修改数据集
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    version = versioner.save_version(sample_dataset, description="v2")
    assert version == 2


@pytest.mark.unit
def test_save_version_creates_files(versioner, sample_dataset):
    """保存版本应创建数据集文件和版本快照"""
    versioner.save_version(sample_dataset, description="v1")
    # 当前版本文件
    assert os.path.exists(versioner._dataset_path("test_ds"))
    # 版本快照
    assert os.path.exists(versioner._version_path("test_ds", 1))
    # 变更日志
    assert os.path.exists(versioner._changelog_path("test_ds"))


@pytest.mark.unit
def test_save_version_empty_name_rejected(versioner):
    """空名称应被拒绝"""
    ds = GroundTruthDataset(name="", description="空名称")
    with pytest.raises(ValueError, match="名称"):
        versioner.save_version(ds)


@pytest.mark.unit
def test_save_version_atomic_write(versioner, sample_dataset):
    """保存后不应残留临时文件（戒律 P4: 原子写入）"""
    versioner.save_version(sample_dataset, description="v1")
    base_dir = versioner.base_dir
    versions_dir = versioner.versions_dir
    # 检查无 .tmp 文件残留
    for fname in os.listdir(base_dir):
        assert not fname.endswith(".tmp"), f"残留临时文件: {fname}"
    for fname in os.listdir(versions_dir):
        assert not fname.endswith(".tmp"), f"残留临时文件: {fname}"


# ============================================================
# 列出版本测试
# ============================================================
@pytest.mark.unit
def test_list_versions_empty(versioner):
    """空数据集应返回空列表"""
    assert versioner.list_versions("nonexistent") == []


@pytest.mark.unit
def test_list_versions_ordered_desc(versioner, sample_dataset):
    """版本应按版本号倒序"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    versions = versioner.list_versions("test_ds")
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1


@pytest.mark.unit
def test_list_versions_contains_description(versioner, sample_dataset):
    """版本信息应包含描述"""
    versioner.save_version(sample_dataset, description="初始版本描述")
    versions = versioner.list_versions("test_ds")
    assert versions[0]["description"] == "初始版本描述"


# ============================================================
# 获取版本测试
# ============================================================
@pytest.mark.unit
def test_get_version_returns_dataset(versioner, sample_dataset):
    """获取指定版本应返回数据集"""
    versioner.save_version(sample_dataset, description="v1")
    ds = versioner.get_version("test_ds", 1)
    assert ds is not None
    assert ds.name == "test_ds"
    assert len(ds.records) == 2


@pytest.mark.unit
def test_get_nonexistent_version_returns_none(versioner, sample_dataset):
    """不存在的版本应返回None"""
    versioner.save_version(sample_dataset, description="v1")
    assert versioner.get_version("test_ds", 99) is None


@pytest.mark.unit
def test_get_latest_version(versioner, sample_dataset):
    """获取最新版本"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    latest = versioner.get_latest_version("test_ds")
    assert latest is not None
    assert len(latest.records) == 3  # v2 有3条记录


@pytest.mark.unit
def test_get_latest_version_empty(versioner):
    """无版本时应返回None"""
    assert versioner.get_latest_version("nonexistent") is None


# ============================================================
# 版本对比测试
# ============================================================
@pytest.mark.unit
def test_compare_versions_detects_added(versioner, sample_dataset):
    """对比应检测新增记录"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    diff = versioner.compare_versions("test_ds", 1, 2)
    assert "T3" in diff["diff"]["added"]
    assert diff["diff"]["removed"] == []


@pytest.mark.unit
def test_compare_versions_detects_removed(versioner, sample_dataset):
    """对比应检测删除记录"""
    versioner.save_version(sample_dataset, description="v1")
    # 删除一条记录
    del sample_dataset.records["T1"]
    versioner.save_version(sample_dataset, description="v2")

    diff = versioner.compare_versions("test_ds", 1, 2)
    assert "T1" in diff["diff"]["removed"]


@pytest.mark.unit
def test_compare_versions_detects_modified(versioner, sample_dataset):
    """对比应检测修改记录"""
    versioner.save_version(sample_dataset, description="v1")
    # 修改一条记录
    sample_dataset.records["T1"].is_suspicious = False
    sample_dataset.records["T1"].review_status = "reviewed"
    versioner.save_version(sample_dataset, description="v2")

    diff = versioner.compare_versions("test_ds", 1, 2)
    assert "T1" in diff["diff"]["modified"]


@pytest.mark.unit
def test_compare_versions_includes_stats(versioner, sample_dataset):
    """对比结果应包含两个版本的统计"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    diff = versioner.compare_versions("test_ds", 1, 2)
    assert "stats_a" in diff
    assert "stats_b" in diff
    assert diff["stats_b"]["total_records"] > diff["stats_a"]["total_records"]


@pytest.mark.unit
def test_compare_nonexistent_version_raises(versioner, sample_dataset):
    """对比不存在的版本应抛异常"""
    versioner.save_version(sample_dataset, description="v1")
    with pytest.raises(ValueError, match="不存在"):
        versioner.compare_versions("test_ds", 1, 99)


# ============================================================
# 版本回滚测试
# ============================================================
@pytest.mark.unit
def test_rollback_creates_new_version(versioner, sample_dataset):
    """回滚应创建新版本（戒律 M4: 保留回滚痕迹）"""
    v1 = versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    v2 = versioner.save_version(sample_dataset, description="v2")

    # 回滚到 v1
    v3 = versioner.rollback_to_version("test_ds", v1)

    assert v3 == 3  # 新版本号
    # 验证回滚后的数据等于 v1
    ds_v3 = versioner.get_version("test_ds", v3)
    assert len(ds_v3.records) == 2  # v1 有2条记录


@pytest.mark.unit
def test_rollback_preserves_history(versioner, sample_dataset):
    """回滚不应删除历史版本（戒律 M4: 可追溯）"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    versioner.rollback_to_version("test_ds", 1)

    # v1 和 v2 仍应存在
    assert versioner.get_version("test_ds", 1) is not None
    assert versioner.get_version("test_ds", 2) is not None
    # 回滚创建的 v3 也应存在
    assert versioner.get_version("test_ds", 3) is not None


@pytest.mark.unit
def test_rollback_nonexistent_version_raises(versioner, sample_dataset):
    """回滚到不存在的版本应抛异常"""
    versioner.save_version(sample_dataset, description="v1")
    with pytest.raises(ValueError, match="不存在"):
        versioner.rollback_to_version("test_ds", 99)


@pytest.mark.unit
def test_rollback_description_recorded(versioner, sample_dataset):
    """回滚描述应记录在变更日志中"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    versioner.rollback_to_version("test_ds", 1, description="误删记录，回滚")

    changelog = versioner.get_changelog("test_ds")
    latest = changelog["versions"][-1]
    assert "误删记录，回滚" in latest["description"]


# ============================================================
# 变更日志测试
# ============================================================
@pytest.mark.unit
def test_changelog_empty_for_new_dataset(versioner):
    """新数据集的变更日志应为空"""
    changelog = versioner.get_changelog("nonexistent")
    assert changelog["versions"] == []
    assert changelog["latest_version"] == 0


@pytest.mark.unit
def test_changelog_records_all_versions(versioner, sample_dataset):
    """变更日志应记录所有版本"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    changelog = versioner.get_changelog("test_ds")
    assert len(changelog["versions"]) == 2
    assert changelog["latest_version"] == 2
    assert changelog["versions"][0]["version"] == 1
    assert changelog["versions"][1]["version"] == 2


@pytest.mark.unit
def test_changelog_includes_changes(versioner, sample_dataset):
    """变更日志应包含差异信息"""
    versioner.save_version(sample_dataset, description="v1")
    sample_dataset.add_record(GroundTruthRecord("T3", is_suspicious=True))
    versioner.save_version(sample_dataset, description="v2")

    changelog = versioner.get_changelog("test_ds")
    v2_entry = changelog["versions"][1]
    assert "changes" in v2_entry
    assert "T3" in v2_entry["changes"]["added"]


@pytest.mark.unit
def test_changelog_first_version_all_added(versioner, sample_dataset):
    """首版本的变更应为全部新增"""
    versioner.save_version(sample_dataset, description="v1")

    changelog = versioner.get_changelog("test_ds")
    v1_entry = changelog["versions"][0]
    assert "T1" in v1_entry["changes"]["added"]
    assert "T2" in v1_entry["changes"]["added"]
    assert v1_entry["changes"]["removed"] == []


# ============================================================
# 删除版本测试
# ============================================================
@pytest.mark.unit
def test_delete_version_removes_snapshot(versioner, sample_dataset):
    """删除版本应移除快照文件"""
    versioner.save_version(sample_dataset, description="v1")
    snapshot_path = versioner._version_path("test_ds", 1)
    assert os.path.exists(snapshot_path)

    assert versioner.delete_version("test_ds", 1) is True
    assert not os.path.exists(snapshot_path)


@pytest.mark.unit
def test_delete_nonexistent_version_returns_false(versioner):
    """删除不存在的版本应返回False"""
    assert versioner.delete_version("nonexistent", 1) is False


@pytest.mark.unit
def test_delete_version_zero_returns_false(versioner):
    """删除版本0应返回False（无效版本号）"""
    assert versioner.delete_version("test_ds", 0) is False


# ============================================================
# 戒律验证测试
# ============================================================
@pytest.mark.unit
def test_no_fabricated_data_in_changelog(versioner, sample_dataset):
    """变更日志不应有编造标记（戒律 M1）"""
    versioner.save_version(sample_dataset, description="真实标注")
    changelog = versioner.get_changelog("test_ds")
    changelog_str = json.dumps(changelog, ensure_ascii=False)
    assert "编造" not in changelog_str
    assert "假数据" not in changelog_str


@pytest.mark.unit
def test_changelog_atomic_write(versioner, sample_dataset):
    """变更日志写入后不应残留临时文件（戒律 P4）"""
    versioner.save_version(sample_dataset, description="v1")
    versions_dir = versioner.versions_dir
    for fname in os.listdir(versions_dir):
        assert not fname.endswith(".tmp")
