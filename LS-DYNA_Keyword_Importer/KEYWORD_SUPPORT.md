# 关键字支持矩阵（v1.2.1）

状态含义：

- **直接**：有明确 Abaqus 输入关键字映射；仍需核对单位和求解器差异。
- **近似**：保留主要意图，但物理公式或高级参数不同。
- **报告**：读取并记录，不主动写入求解模型。
- **未支持**：写入转换报告；`STOP` 模式下停止。

## 项目组织与预处理

| LS-DYNA | Abaqus/插件行为 | 状态 |
|---|---|---|
| `*INCLUDE` | 按出现位置递归展开 | 直接 |
| `*INCLUDE_PATH` | 加入当前项目的包含搜索路径 | 直接 |
| `*INCLUDE_TRANSFORM` | 读取被包含文件；不施加变换 | 近似 |
| `*INCLUDE_BINARY` | 不解析二进制负载 | 未支持 |
| `*PARAMETER` | R/I/C 参数替换，支持一卡多参数 | 直接 |
| `*PARAMETER_EXPRESSION` | `+ - * / ** %` 基础算术 | 近似 |
| `*TITLE` | `*HEADING` | 直接 |

## 网格、部件与截面

| LS-DYNA | Abaqus | 状态 |
|---|---|---|
| `*NODE` | `*NODE` | 直接 |
| `*ELEMENT_SOLID` | `C3D4/C3D5/C3D6/C3D8R`（按唯一节点数） | 直接 |
| `*ELEMENT_SHELL` | `S3/S4R` | 直接 |
| `*ELEMENT_TSHELL` | `SC8R` 或连续体退化映射 | 近似 |
| `*ELEMENT_BEAM` | `B31` | 近似 |
| `*ELEMENT_SEATBELT` | `B31` | 近似 |
| `*ELEMENT_SPH` | `PC3D` | 近似 |
| `*PART` | 每个有单元的 PID 建独立 `*PART/*INSTANCE`；跨 Part 集合在 Assembly 重建 | 直接 |
| `*SECTION_SOLID` ELFORM=1 | `C3D*` + `*SOLID SECTION` | 直接 |
| `*SECTION_SOLID` ELFORM=5/6/7/11/12（AUTO/EULERIAN） | `EC3D8R` + 独立 `*EULERIAN SECTION` | 近似 |
| `*SECTION_SHELL` | `*SHELL SECTION`；采用节点厚度平均 | 近似 |
| `*SECTION_BEAM` | `*BEAM GENERAL SECTION` | 近似 |
| 多 PID 共用节点 | 默认用 `*MPC` + `TIE` 拼合独立 Lagrangian Part；可选旧 `EQUATION` 或 `NONE` | 默认模式同时处理两端公共自由度；CEL 节点不耦合 |
| `*ALE_STRUCTURED_MESH_CONTROL_POINTS` | 插值生成 SALE 三方向节点坐标 | 直接 |
| `*ALE_STRUCTURED_MESH` | 展开为结构化八节点实体网格 | 直接拓扑；求解格式近似 |
| `*ALE_STRUCTURED_MESH_REFINE` | 按 NX/NY/NZ 细分控制点区间 | 直接 |
| `*DEFINE_COORDINATE_NODES`（SALE） | 初始局部方向；运动坐标系只取初态 | 近似 |

单元积分公式、沙漏形式、删除方式和单元方向不自动视为等价。

## 集合与表面

| LS-DYNA | Abaqus | 状态 |
|---|---|---|
| `*SET_NODE_LIST[_TITLE/_ADD]` | `*NSET` | 直接 |
| `*SET_NODE_LIST_GENERATE` | 展开后 `*NSET` | 直接 |
| `*SET_SOLID/SET_SHELL/SET_BEAM[_ADD]` | `*ELSET` | 直接 |
| `*SET_PART_LIST[_ADD]` | 展开 PID 后 `*ELSET` | 直接 |
| `*SET_SEGMENT[_TITLE/_ADD]` | 通过节点集合反查 `*SURFACE, TYPE=ELEMENT` | 直接；找不到面则警告 |
| `*SET_MULTI_MATERIAL_GROUP...` | ALE domain ELSET | 近似 |
| Box、General 等程序化集合 | 无通用安全展开 | 未支持 |
| `*SET_NODE_GENERAL` + `SALEFAC/SALECPT` | SALE 节点选择展开为 `*NSET` | 直接 |
| `*SET_SOLID_GENERAL` + `SALEFAC/SALECPT` | SALE 单元选择展开为 `*ELSET` | 直接 |
| `*SET_SEGMENT_GENERAL` + `SALEFAC/SALECPT` | SALE 边界展开为元素面 | 直接 |

## 材料与 EOS

| LS-DYNA | Abaqus | 状态 |
|---|---|---|
| `*MAT_ELASTIC` / `*MAT_001` | `*DENSITY` + `*ELASTIC` | 直接 |
| `*MAT_PLASTIC_KINEMATIC` / `*MAT_003` | 弹性 + `*PLASTIC, HARDENING=COMBINED` | 近似 |
| `*MAT_PIECEWISE_LINEAR_PLASTICITY` / `*MAT_024` | 弹性 + `*PLASTIC`，可引用应力-塑性应变曲线 | 近似 |
| `*MAT_JOHNSON_COOK` / `*MAT_015` | Johnson–Cook 硬化与率项 | 近似；失效需复核 |
| `*MAT_NULL` / `*MAT_009` | 密度 + US-UP EOS 基础回退 | 近似 |
| `*MAT_HIGH_EXPLOSIVE_BURN` / `*MAT_008` + `*EOS_JWL` | 密度 + Abaqus `*EOS, TYPE=JWL`，爆速取 MAT 的 D | 直接/近似 |
| `*INITIAL_DETONATION` | 紧随 JWL 的 `*DETONATION POINT` | 直接；缺失时生成部件中心点并警告 |
| `*EOS_GRUNEISEN` / `*EOS_004` | Abaqus `*EOS, TYPE=USUP` 的 C、S1、Gamma0 | 近似；S2/S3 报告 |
| `*MAT_ADD_EROSION` | 塑性 EFFEPS → `*SHEAR FAILURE`；EOS 拉伸截止 → `*TENSILE FAILURE`；其余参数注释/报告 | 近似 |
| `*MAT_ISOTROPIC_ELASTIC_PLASTIC` / `*MAT_012` | 弹性 + 各向同性塑性 | 近似 |
| `*MAT_POWER_LAW_PLASTICITY` / `*MAT_018` | 弹性 + 两点幂律塑性近似 | 近似 |
| `*MAT_MOONEY_RIVLIN_RUBBER` / `*MAT_027` | Abaqus Mooney-Rivlin | 近似 |
| `*MAT_RIGID` / `*MAT_020` | 刚性材料参数回退；刚体关系需人工确认 | 近似 |
| 其他已定义且被使用的 `*MAT_*` | 保留密度及可识别弹性常数，生成明确的弹性保底材料并报告 | 近似 |
| PART 引用但未定义的 MID | 占位密度/弹性材料并报告 | 近似/错误修复 |
| `*EOS_IDEAL_GAS` | 无法从通用卡可靠恢复 Abaqus 气体常数时使用初始体积模量 US-UP 保底 | 近似；报告 |
| `*EOS_LINEAR_POLYNOMIAL` | 由 C1/密度生成初始声速的 US-UP 近似 | 近似 |

## 曲线、边界、初始条件与载荷

| LS-DYNA | Abaqus | 状态 |
|---|---|---|
| `*DEFINE_CURVE[_TITLE]` | `*AMPLITUDE, TIME=TOTAL TIME`，应用 SFA/SFO/OFFA/OFFO | 直接 |
| `*BOUNDARY_SPC_NODE` | `*BOUNDARY` | 直接；非零 CID 警告 |
| `*BOUNDARY_SPC_SET` | `*BOUNDARY` on NSET | 直接；非零 CID 警告 |
| `*BOUNDARY_PRESCRIBED_MOTION_NODE/SET` | 位移/速度/加速度边界 + amplitude | 直接；birth/death 需复核 |
| `*INITIAL_VELOCITY_NODE` | `*INITIAL CONDITIONS, TYPE=VELOCITY` | 直接 |
| `*INITIAL_VELOCITY_GENERATION` | PID ELSET 初速度 | 近似 |
| `*LOAD_NODE_POINT` | `*CLOAD` | 直接 |
| `*LOAD_NODE_SET` | `*CLOAD` on NSET | 直接 |
| `*LOAD_BODY_X/Y/Z` | `*DLOAD, GRAV` | 近似 |
| `*LOAD_SEGMENT` | 节点面反查后压力载荷 | 近似 |
| 复杂刚体、爆炸、热、流入流出载荷 | 无通用安全映射 | 未支持 |

## 接触与约束

| LS-DYNA | Abaqus | 状态 |
|---|---|---|
| `*CONTACT_AUTOMATIC_SINGLE_SURFACE` | all-exterior general contact | 近似 |
| `*CONTACT_AUTOMATIC_GENERAL` | all-exterior general contact | 近似 |
| `*CONTACT_AUTOMATIC_SURFACE_TO_SURFACE` | 可解析时 `*CONTACT PAIR`，否则 general contact | 直接/近似 |
| `*CONTACT_AUTOMATIC_NODES_TO_SURFACE` | 节点面 + 元素面 contact pair | 直接/近似 |
| `*CONTACT_TIED_*` | `*TIE` | 近似 |
| `*CONTACT_*_TIEBREAK` | 永久 `*TIE`；失效准则不复制 | 近似，必须人工重建 |
| `*CONSTRAINED_LAGRANGE_IN_SOLID` | 通过 general contact 表达主要耦合意图 | 近似/报告 |
| 摩擦 FS | `*FRICTION` | 直接 |
| FD/DC/VC/VDC、SOFT、SLSFAC、侵蚀参数 | 转换报告 | 报告 |

## ALE 与控制

| LS-DYNA | Abaqus | 状态 |
|---|---|---|
| `*CONTROL_TERMINATION` | Explicit step 终止时间 | 直接 |
| `*CONTROL_TIMESTEP` | 使用 Abaqus 稳定时间步；原参数写入报告 | 近似 |
| `*CONTROL_ALE` | AUTO 按 SECTION ELFORM 分流 CEL；ADAPTIVE 写 `*ADAPTIVE MESH` | 近似 |
| `*ALE_REFERENCE_SYSTEM_GROUP` | ALE 域意图记录 | 近似/报告 |
| `*ALE_MULTI-MATERIAL_GROUP`（兼容下划线别名） | ALE/CEL 域 PID 提取及 Abaqus Eulerian section 材料列表 | 近似 |
| `*INITIAL_VOLUME_FRACTION_GEOMETRY` | TYPE 6 球、TYPE 3 半空间和 TYPE 1 盒体按单元中心生成 EVF | 近似 |
| `*ALE_STRUCTURED_FSI` | CEL all-exterior general contact | 近似 |
| `*ALE_STRUCTURED_MESH_MOTION` | 初始网格保留；运动规律写入报告 | 报告 |
| `*ALE_ESSENTIAL_BOUNDARY` | 写入报告，需人工重建 Eulerian 边界 | 报告 |
| 其他 `*CONTROL_*` | 使用 Abaqus 默认算法，原卡写入报告 | 报告 |
| `*HOURGLASS` + PART HGID | `*SECTION CONTROLS`，按 IHQ 选 VISCOUS/STIFFNESS/ENHANCED 并归一化 QM/Q1/Q2 | 近似 |

## 输出请求

| LS-DYNA | Abaqus | 状态 |
|---|---|---|
| `*DATABASE_BINARY_D3PLOT` | Field node/element output | 直接 |
| `*DATABASE_BINARY_D3THDT` | History output | 直接 |
| `*DATABASE_NODOUT` | Node output | 直接 |
| `*DATABASE_ELOUT/SLEOUT` | Element output | 直接 |
| `*DATABASE_GLSTAT/MATSUM` | Energy history output | 直接/近似 |
| `*DATABASE_RCFORC/NCFORC/INTFOR` | Contact output | 近似 |
| `*DATABASE_HISTORY_NODE` | 专用历史 NSET | 直接 |
| `*DATABASE_HISTORY_SOLID/SHELL` | 专用历史 ELSET | 直接 |
| `*DATABASE_EXTENT_*` / `*DATABASE_FORMAT` | ODB 中无对应二进制布局含义 | 报告 |

## 扩展位置

新增关键字处理主要位于：

- `lsk_parser.py`：文件、包含、参数和数据行读取；
- `lsk_converter.py` 中 `LsdynaSemanticReader`：LS-DYNA 语义解析；
- `lsk_converter.py` 中 `AbaqusInpWriter`：Abaqus 输入关键字输出。

每个新映射必须同时添加：转换状态、警告语义和自动测试，避免“静默成功”。
