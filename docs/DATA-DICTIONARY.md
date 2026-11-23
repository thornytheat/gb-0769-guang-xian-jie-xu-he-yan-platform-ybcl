# 轨迹、接头与判损字段

A、B 的 CSV 必填 `event,distance,unit,loss_db`，设计清单必填 `joint,distance,unit,limit_db`。字段可换列序，UTF-8 首行 BOM 会清理。ID 为原形字符串，按来源和 ID 分键。同键比较归一距离与损耗，完全相同保留首行，冲突整组隔离。记录保留起始物理行和原值。空白单元格、哨兵及 JSON null 分别校验。空文件、全空行、仅表头分别为 `EMPTY_FILE`、`BLANK_FILE`、`HEADER_ONLY`。每源最多 350 个正文记录、250,000 字符。

距离单位可为 mm、m 或 km，归一成整数毫米。采集损耗 dB 支持有符号值，精确到毫 dB。配置 `length_m` 为光路总长，`zero_a_m/zero_b_m` 为两向测量偏置。统一链程 A = A 原读数减 A 起偏，B = 全长减去 B 原读数扣掉 B 起偏的结果。校准有效范围是闭区间 `[0,L]`。出界测量归隔离，出界设计仍在清单中并标缺证。设计本来就在 A 端坐标系。

双端事件按链程和 ID 排序。一对一单调配对先最大化数量，再最小化距离差总和，最后依照同状态优先配对、保留较早 A 端、再保留较早 B 端的固定遍历作稳定裁决。容差 `pair_tol_m` 含等号。配对中点的分辨率为半毫米。配对双方的读数取带符号平均作为净损耗，保留单向负值和原行。通过阈值含等号，净值为负记录无效净增益。

设计点与配对中点再次按相同优化目标一对一对齐，使用 `design_tol_m`。每接头以自己的 `limit_db` 判定，损耗模块的 `reference_limit_db` 用于单独预览。单端或孤配均保留。运行清单含源 SHA-256、校准参数、时间 UTC、耗时、版本、证据。快照摘要可用排序键、紧凑 JSON 和 UTF-8 重算。

## 仪表复校与交接判定

留档请求可在 `metrology` 下按端携带 `{"a":{"meter_id","measured_at"},"b":{...}}`，两字段均为去空白后的非空字符串；`measured_at` 必须是带时区的 ISO-8601，统一折成 UTC 秒。仪表信息不参与核验件号（件号仍只由 SPEC、源摘要和校准参数决定），只作为该件的留档元数据；同件重跑只能补登缺失端，对已留档端给出不同仪表或时间返回 409 `METROLOGY_CONFLICT`。旧签发件没有 `metrology` 节时，两端均判 `METER_INFO_MISSING` 并暂缓交接。

`POST /api/meters` 登记某仪表某端的失准窗口（闭区间，含起止），载荷 `meter_id`、`end`（a/b）、`out_of_tolerance_windows:[{start,end}]`；窗口必填、起止须带时区且止晚于起，同载荷内重叠报 `OVERLAPPING_WINDOW`。窗口只追加：同仪表同端再次登记时，已存在的窗口原样保留，新窗口并入并按起始排序，跨次重叠同样拒绝；完全重复的载荷不写入。窗口按端隔离，同一仪表编号在 A、B 两端的窗口互不影响。

`GET /api/inspections/<id>/handover` 对已签件逐端比对该端仪表在实测时刻是否落在失准窗口内：命中 `OUT_OF_TOLERANCE_WINDOW`、仪表未在该端登记为 `METER_UNREGISTERED`、缺仪表信息为 `METER_INFO_MISSING`，均暂缓；两端都合格才 `CLEARED`。任一端暂缓则整件 `HOLD`。判定是只读追加视图，不回写、不改变原签发结论；`original_verdict` 随响应留档。迟到的仪表/实测信息通过 `POST /api/inspections/<id>/metrology` 追到已签件，同载荷重试 `updated:false`，不产生新件、不重复登记。
