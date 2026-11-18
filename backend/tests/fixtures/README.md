# 真实公开测试样本

`uci_excerpt.csv` 为 UCI Online Retail 官方文件前5000条中的前12条普通记录及前3条取消记录。保留原始行号，不包含 CustomerID，没有生成虚构交易或成本。

来源：Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning Repository. DOI: [10.24432/C5BW33](https://doi.org/10.24432/C5BW33)。数据许可：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。修改：移除客户标识，字段重命名，加入原始行号和GBP币种，按Europe/London解释原始时间。

`uci_control.json` 保存固定摘录的行数、数量、金额和SHA256，用于离线CI对账。完整来源和原文件摘要见仓库 `data/public_manifest.json`。
