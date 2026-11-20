# 澄浦双口流量比测复核账

白班技师从采集器接收读数，从工装接线表取得公开通道。夜班停源后凭指定校准件核定出口除以入口的可达变比。值班负责人按原报告号核对结论、父件和回执。

## 运行

使用 Python 3.11 标准库。项目由控制台物化后，从项目根目录运行完整回归和演示。

```text
python3 -B check_project.py
python3 -B demo.py workday
```

一天剧本使用 `samples/batch.json` 和 `samples/correction.json`。它在临时目录创建台架库与独立归档库，入账并冻结，按残差上限 1 核算，签出校准件并关源，将件正文通过标准输入交给离线进程，签发两份变比报告，再更正、另签新件并按最初报告号复领。演示输出实际集合和原回执。样例由演示显式录入。

离线进程接受单次申请。`python3 -B demo.py offline ARCHIVE INLET OUTLET` 从标准输入读取一份校准件 JSON 正文，成功时在标准输出返回正式报告和回执的 JSON。

## Python 接口

1. I1 使用 `bench.store.BenchLedger(connection)`。调用 `initialize()` 后，以 `accept_batch(columns, measurements, wiring)` 接纳样例形式的整批输入。每条读次含 `key`、`value`、`unit`、`design`，每条接线含 `name`、`offset`、`unit`、`coefficients`。`correct_readings(corrections)` 按已有读次键更正数值及单位，设计行和接线随该批保持。整数、整数字符串、有限十进制字符串、分数字符串和 `Fraction` 是精确输入。空值、浮点数、重名和未知单位拒绝。名称须非空且无首尾空白。设计系数按提交的私有列序解释。读次和通道按名称稳定排序。
2. `freeze_current()` 返回持久化的 `FrozenRevision`，其 `revision_id` 可交给 `load_revision(revision_id)`。同一工作版本再次冻结会领取原修订。有效更正产生新工作版本，改回旧内容时可以得到相同指纹和不同修订号。语义相同的重放保留工作版本。空队列和未知修订返回查找错误。
3. I2 使用 `calibration.fit.fit_frozen_revision(revision, limit)`。在实参数上拟合 `Xβ≈y`，返回精确中心、正规矩阵、最小残差平方和及余量。设计列必须满秩。预算为未加权残差平方和上限，单位为 `(mL/s)²`。预算恰好等于最低残差时仍有一个可行校准点。非法有理输入、秩不足和预算不足分别以 `ValueError` 或其业务子类报告。
4. I3 使用同一台架连接上的 `evidence.register.IssueRegister`，初始化后调用 `evidence.issue.issue_calibration(fitted, register, wiring=None)`。省略接线时采用该修订的归一接线。显式接线为 `ChannelDefinition` 序列，其定义必须与冻结修订一致。签出后 `register.get(artifact_id)` 返回原件正文。正文版本为 1，含件号、修订号、修订指纹、具名目录、公开中心和有理对称半正定形状矩阵。中心和形状定义同一次校准的完整闭联合域，零秩、低秩和重复读值都可表达。原测、设计行和私有参数名留在台架库。
5. I4 使用独立归档连接。调用 `reports.publish.initialize_archive(connection)` 后，以 `review.assess.assess_and_publish(connection, artifact_text, RatioRequest(inlet, outlet))` 定稿。其中 `RatioRequest` 定义于 `review.request`。返回 `(ReportRecord, Receipt)`。两路均取自指定件，只有实际可同时取到且入口非零的读值对支持一个有限变比。同一通道可作为两路输入。未知通道抛出 `UnknownChannelError`，非法封套抛出 `ValueError`，SQLite 发布错误向调用者传播。报告和回执共同提交，失败可重试。
6. I5 使用 `reports.retrieve.retrieve_report(connection, report_id)`，返回已提交的原报告及其回执，未知号抛出 `ReportNotFound`。报告保存原件号、原修订及指纹、实际入口出口和原集合。读取只访问归档。源库及归档初始化均可重复执行。写入入口自行拥有事务，传入连接应处于空闲状态。

## 计算和归档口径

`mL/s` 是统一流量单位。`L/min` 精确乘 `50/3`，公开接线的截距与系数一起换算，实测零与负流量均合法。数值、判定、排序和编码采用精确运算。导出集合是升序数组，每个分量为 `[下端, 上端, 下端闭合, 上端闭合]`。`null` 是相应方向的无界端且开放。有理端点用约分字符串。非有理二次端点用 `[a,b,c,i]` 表示首系数为正的本原整数多项式 `a·r²+b·r+c` 的第 `i` 个实根，低根索引为 0。闭合且两端相等表示孤点，空数组表示空集。`RatioSet.contains()` 用精确值核对成员。

回归覆盖混合单位及恰预算签发、重复通道及非法单位拒绝、秩不足及预算不足、两种同边缘接线的断源复核、零入口、比例孤点、代数边界复领、更正和重复初始化、未知通道及报告号、两次写入之间与提交阶段的事务回滚。

## V2 联合流量容差

夜班关站后只凭单件正文核定具名闭区间组的精确最低总残差平方和。V2 件与 V1 共用台架、登记和归档库，正文 `version` 为 `2`，原测、设计行和私有参数名仍留站。

7. I6 使用 `evidence.issue.issue_tolerance_calibration(fitted, register)`，按冻结修订的归一接线签发 V2 件，`register.get(artifact_id)` 领取登记原文，未知件号仍为 `KeyError`。正文含件号、修订号、修订指纹、具名通道、公开中心、有理对称半正定费用形状 `cost_shape`、残差底数 `residual_floor` 和原预算 `budget`。费用形状为 `M = W G^-1 W'`，与公开读值偏移 `q` 一起精确决定每个可达读值组合的最低费用 `residual_floor + q' M^+ q`；零余量不影响 `M`。混入 V1 件、重复 JSON 字段、缺失或非精确证据、与费用形状不自洽的封套均拒绝。
8. I7 使用独立归档连接上的 `review.assess.assess_tolerance_and_publish(connection, artifact_text, intervals)`。`intervals` 是非空列表或元组，每项为 `[name, lower, upper]` 或同形元组，允许单点（两端相等）和省略通道；端点接受非布尔整数、有理字符串和 `Fraction`。重复或未知通道、反向区间、非法端点和非法结构报 `ValueError` 且无本次成果。恒定和线性依赖通道由费用形状的零行与降秩表达自动处理；整组由同一次校准满足，全域无解时 `minimum_residual_sum` 为 `None`、`within_budget` 为 `False`，仍签发报告。
9. I8 使用 `reports.retrieve.retrieve_tolerance_report(connection, report_id)` 按号领取原报告和回执，未知号报 `reports.retrieve.ReportNotFound`。报告属性为 `report_id`、`artifact_id`、`revision_id`、`revision_fingerprint`、`limit`、`intervals`、`minimum_residual_sum`、`within_budget`；`intervals` 按通道名升序，端点为 `Fraction`。报告与回执在同一 SQLite 事务提交，写入失败无部分成果且可重试。`python3 -B demo.py tolerance` 演示断源签发、勘误后另签和重开按原报告号领取，`offline-tolerance` 子命令从标准输入读取 `{"artifact", "intervals"}` 载荷。
