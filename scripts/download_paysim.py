"""
Kaggle PaySim 数据集下载器

使用方法:
    python scripts/download_paysim.py

需要:
    pip install kagglehub

说明:
    PaySim 数据集 (~470MB) 来自 Kaggle，包含 636 万条交易。
    下载后自动保存到 data/PS_20174392719_1491204439457_log.csv，
    之后 evaluate.py 和 dataset_builder.py 可自动加载真实数据。
"""
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TARGET_FILE = os.path.join(DATA_DIR, "PS_20174392719_1491204439457_log.csv")


def download_with_kagglehub():
    """使用 kagglehub 下载"""
    try:
        import kagglehub
    except ImportError:
        print("请先安装 kagglehub: pip install kagglehub")
        return False

    print("正在从 Kaggle 下载 PaySim 数据集 (~470MB)...")
    path = kagglehub.dataset_download("ntnu-testimon/paysim1")
    csv_file = os.path.join(path, "PS_20174392719_1491204439457_log.csv")

    if os.path.exists(csv_file):
        shutil.copy(csv_file, TARGET_FILE)
        size_mb = os.path.getsize(TARGET_FILE) / (1024 * 1024)
        print(f"下载完成: {TARGET_FILE} ({size_mb:.1f} MB)")
        return True
    return False


def download_with_requests():
    """使用 requests 直接下载（备用方案，需要 Kaggle API 认证）"""
    import requests
    url = "https://www.kaggle.com/api/v1/datasets/ntnu-testimon/paysim1/download"
    print(f"备用方案: 使用 requests 下载...")
    print(f"提示: 需要设置 KAGGLE_USERNAME 和 KAGGLE_KEY 环境变量")
    print(f"或从浏览器下载: https://www.kaggle.com/datasets/ntnu-testimon/paysim1")

    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")
    if not username or not key:
        print("未设置 Kaggle 认证，跳过")
        return False

    import io, zipfile
    resp = requests.get(url, auth=(username, key), stream=True)
    if resp.status_code != 200:
        print(f"下载失败: HTTP {resp.status_code}")
        return False

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(DATA_DIR)
    print(f"下载完成: {TARGET_FILE}")
    return True


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(TARGET_FILE):
        size_mb = os.path.getsize(TARGET_FILE) / (1024 * 1024)
        print(f"PaySim 数据集已存在: {TARGET_FILE} ({size_mb:.1f} MB)")
        print("如需重新下载，请先删除此文件")
        return

    print("=" * 60)
    print("Kaggle PaySim 数据集下载")
    print("=" * 60)
    print("数据集: Synthetic Financial Datasets For Fraud Detection")
    print("来源: https://www.kaggle.com/datasets/ntnu-testimon/paysim1")
    print("大小: ~470MB (636万条交易)")
    print()

    success = download_with_kagglehub()
    if not success:
        print()
        print("自动下载失败。手动下载步骤:")
        print("1. 访问 https://www.kaggle.com/datasets/ntnu-testimon/paysim1")
        print("2. 点击 Download 按钮")
        print(f"3. 将 CSV 文件放入: {DATA_DIR}")
        print(f"4. 文件名应为: PS_20174392719_1491204439457_log.csv")


if __name__ == "__main__":
    main()
