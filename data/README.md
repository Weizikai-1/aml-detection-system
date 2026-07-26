# 数据目录

## 数据来源

### 1. PaySim 合成交易数据(推荐)
- 下载地址: https://www.kaggle.com/datasets/ealaxi/paysim1
- 数据量: 635 万条交易
- 字段: step, type, amount, nameOrig, oldbalanceOrig, newbalanceOrig, nameDest, oldbalanceDest, newbalanceDest, isFraud, isFlaggedFraud

### 2. 自合成数据(第1周备选)
使用 data_generator.py 生成模拟交易数据,包含标注的洗钱案例。

## 使用方式
1. 下载 PaySim 数据,重命名为 paysim_data.csv 放到本目录
2. 或运行 python data/data_generator.py 生成模拟数据
