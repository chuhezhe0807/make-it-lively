# PRD: 关节化动画改进（Articulated Animation）

## Introduction

当用户对包含动物或角色的图片下达"让小猫原地奔跑"类指令时，系统表现为整只猫蹦跳而非腿部运动。根因是两层缺陷叠加：

1. **感知层**：VLM 未将猫拆分为子部件（头、身体、前腿、后腿、尾巴），导致整只猫是一张图层
2. **动画规划层**：planner 对"奔跑"等关节运动没有具体的编排策略，只能用 translate 模拟弹跳

当前系统已有 `parent_id` + `pivot` 子部件架构（M1.5），但 VLM 对动物/角色的拆分引导不足，planner 也缺少关节运动的编排模式。本 PRD 通过增强两端的 prompt 来激活已有架构的能力。

## Goals

- 动物、角色等有肢体的元素被 VLM 拆分为可独立运动的子部件（头、身体、四肢、尾巴等）
- 子部件集合应尽量覆盖父元素 bbox，避免动画时出现空洞
- Planner 对"奔跑/走路/挥手/摇尾巴"等关节动作能生成 rotate + pivot 组合的多步 timeline
- 端到端验证：对测试图中的猫输入"原地奔跑"，腿部产生交替摆动动画

## User Stories

### US-021: 增强 VLM 子部件拆分引导
**Description:** As the system, I need the perception prompt to more aggressively decompose animals, characters, and creatures into sub-parts so that limbs can be animated independently.

**Acceptance Criteria:**
- [ ] `PERCEPTION_PROMPT` 中新增明确的动物/角色分解指引，列举典型案例（猫→head/body/front_legs/hind_legs/tail，人→head/torso/arms/legs）
- [ ] 强调：任何有四肢的生物（动物、人、怪物）都必须拆分肢体，即使静态图中姿态模糊
- [ ] 对测试图 `tests/imgs/test_img.png` 调用 perception API，返回的 elements 中 `black_cat` 有至少 3 个子部件（含 `parent_id`）
- [ ] 每个子部件都有合理的 `pivot` 值（如前腿的 pivot 在肩关节位置）
- [ ] Typecheck passes
- [ ] Tests pass

### US-022: 子部件覆盖度校验
**Description:** As the system, I need a post-perception validation step that warns when the union of child bboxes poorly covers the parent bbox, so we catch bad decompositions early.

**Acceptance Criteria:**
- [ ] 在 perception 路由中新增 `_validate_sub_part_coverage(elements)` 函数
- [ ] 对每个有子部件的父元素，计算子部件 bbox 并集面积占父 bbox 面积的比例
- [ ] 当覆盖率 < 70% 时，在响应中添加 warning 日志（不阻塞请求，仅记录）
- [ ] 覆盖率计算有对应的单元测试（给定父 bbox + 子 bbox 列表 → 计算覆盖率）
- [ ] Typecheck passes
- [ ] Tests pass

### US-023: 增强 Planner 关节运动编排能力
**Description:** As the system, I need the animation planner prompt to understand articulated motion patterns (running, walking, waving, wagging) and generate rotate+pivot timelines for sub-parts instead of whole-body translate.

**Acceptance Criteria:**
- [ ] `PLANNER_PROMPT` 中新增关节运动编排指导，包含具体模式：
  - 奔跑/走路：前腿和后腿围绕 pivot（肩/髋关节）交替 rotate 摆动，设置 loop=true
  - 挥手/摇摆：手臂围绕肩关节 pivot 做正-负角度 rotate 循环
  - 摇尾巴：尾巴围绕尾根 pivot 做小角度 rotate 循环
- [ ] 强调：对子部件优先使用 rotate + pivot，不要对子部件使用 translate（translate 会导致脱离父身体）
- [ ] 给出 timeline 模板示例（如前腿：rotate +15° → rotate -15° → rotate 0°，loop=true）
- [ ] Typecheck passes
- [ ] Tests pass

### US-024: 增加 yoyo 循环支持
**Description:** As a developer, I need a `yoyo` flag in the animation DSL so that oscillating motions (leg swing, tail wag) can be expressed as a single rotate step that automatically reverses, instead of requiring 3 explicit steps.

**Acceptance Criteria:**
- [ ] `ElementAnimation` 模型新增 `yoyo: bool = False` 可选字段
- [ ] 前端 `animator.ts` 中：当 `yoyo=true` 时，GSAP sub-timeline 设置 `yoyo: true, repeat: -1`
- [ ] Planner tool schema 的 `report_animation_plan` 中增加 `yoyo` 字段
- [ ] Planner prompt 中说明 yoyo 适用于摆动类动作（leg swing, tail wag, pendulum）
- [ ] 已有的 loop 测试不受影响（yoyo 默认 false）
- [ ] 新增 animator 单元测试：yoyo=true 的 timeline 设置了 GSAP yoyo+repeat
- [ ] Typecheck passes
- [ ] Tests pass

### US-025: 端到端验证 — 猫原地奔跑
**Description:** As a developer, I need an integration test that verifies the full chain (perception → segment → plan-animation) produces articulated leg motion for the test cat image with prompt "让小猫原地奔跑".

**Acceptance Criteria:**
- [ ] 新增 `tests/e2e/test_articulated_cat.py` 测试文件
- [ ] Mock VLM 返回包含 `black_cat` 父元素 + 至少 `black_cat.front_legs` 和 `black_cat.hind_legs` 子部件的 perception 结果
- [ ] Mock planner 返回的 DSL 中：子部件使用 `rotate` 原语（非 translate），且 pivot 不为 null
- [ ] 验证 planner 的 DSL 中 `black_cat.front_legs` 和 `black_cat.hind_legs` 的 timeline 包含 rotate 步骤
- [ ] 验证父元素 `black_cat` 没有 translate dy（不做整体弹跳）
- [ ] Tests pass
- [ ] Typecheck passes

## Functional Requirements

- **FR-1**: Perception prompt 必须明确列举动物/角色的子部件拆分规则和典型案例
- **FR-2**: 每个子部件的 pivot 应指向关节位置（肩、髋、尾根等），而非 bbox 中心
- **FR-3**: Planner prompt 必须包含关节运动的 timeline 模板（rotate + pivot 组合）
- **FR-4**: Planner 对子部件应优先使用 rotate 而非 translate
- **FR-5**: DSL 新增 yoyo 字段，前端 GSAP 映射正确
- **FR-6**: 所有 prompt 改动向后兼容 — 对无肢体的简单物体（球、文字）行为不变

## Non-Goals (Out of Scope)

- **不做**骨骼绑定或 mesh 变形（仍是 2D 图层旋转）
- **不做**自动检测关节点（依赖 VLM 空间推理能力）
- **不做**前端交互式关节调整
- **不做**多帧精灵动画（frame-by-frame spritesheet）

## Technical Considerations

- Perception prompt 变长可能增加 token 消耗，但 VLM 调用已有缓存机制（相同图片不重复调用）
- VLM 对子部件 bbox 的精度有限，特别是遮挡严重时（如猫蹲坐时腿被身体遮挡）。prompt 应提示 VLM 在这种情况下用估计值
- yoyo 是 GSAP 原生支持的特性，实现成本低
- 子部件拆分后，segment 阶段的 mask 匹配（SAM2 auto + bbox IoU）可能对小部件效果不佳。这是已知限制，不在本 PRD 范围内解决

## Success Metrics

- 对 3 张包含动物/角色的测试图，VLM 正确拆分出肢体子部件的成功率 ≥ 80%
- 对"原地奔跑"prompt，planner 生成 rotate+pivot 腿部动画的成功率 ≥ 90%（vs 当前 0%）
- 用户主观评价"动画生动度"从 2/5 提升至 3.5/5

## Open Questions

- VLM 对不同画风（写实照片 vs 卡通插画 vs 像素画）的子部件拆分能力差异大。是否需要根据画风调整 prompt？（建议先用统一 prompt，后续根据数据调整）
- 当子部件遮挡严重（如侧面猫只露两条腿），VLM 应拆出 2 条腿还是 4 条？（建议拆可见的即可）
