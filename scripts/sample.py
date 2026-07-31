"""一键采样 PaySim — 慢一次，以后秒加载"""
import pandas as pd, numpy as np, os

SRC = r"c:\trae\反洗钱\data\data_table.csv"
OUT = r"c:\trae\反洗钱\data\paysim_sample.csv"
SEED, TARGET = 42, 50000

if os.path.exists(OUT):
    df = pd.read_csv(OUT)
    print(f"样本已存在: {len(df):,} 行, 欺诈 {df.isFraud.sum()} ({df.isFraud.mean()*100:.2f}%)")
    exit()

print("读取完整 PaySim 476MB... (仅此一次)")
np.random.seed(SEED)
df = pd.read_csv(SRC)
df = df.sample(n=TARGET, random_state=SEED).reset_index(drop=True)
df.to_csv(OUT, index=False)
print(f"已保存: {OUT}  |  {len(df):,} 行  |  欺诈 {df.isFraud.sum()} ({df.isFraud.mean()*100:.2f}%)")
