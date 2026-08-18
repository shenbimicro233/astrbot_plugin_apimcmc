# 内置字体资源

本目录中的字体是插件发布包的一部分，字体许可与插件代码的 AGPL-3.0-or-later
许可相互独立。字体文件均保持上游内容，不对字形或 OpenType 表进行修改。

## GNU Unifont 17.0.05

普通中文统计图和查服图使用 GNU Unifont 标准 OpenType 版本（不是区域变体）：

- 来源分发目录：<https://unifoundry.com/pub/>
- 上游文件：<https://unifoundry.com/pub/unifont/unifont-17.0.05/font-builds/unifont-17.0.05.otf>
- 版本：`17.0.05`
- 本地文件：`unifont-17.0.05.otf`
- 文件 SHA-256：`85701ab9b1e251ee16f4df00b13f22eac311d72b7dab427a7d975fe7f5064702`

GNU Unifont 采用 SIL Open Font License 1.1 与带字体嵌入例外的 GNU GPLv2
或更高版本双重许可。完整文本随字体分发：

- `OFL-1.1.txt`：换行归一化后的 SHA-256
  `b7e8b9d8656d5216a61f48c7e62704b8aec50e8af8ca410b28fb04ea32e0f220`
- `LICENSE.txt`：包含版权说明、GPLv2+ 和字体嵌入例外，换行归一化后的 SHA-256
  `1e74cb82bf476843e97c2596297b04219b1a7e51f7238944a8c031cb9401fa87`

## Monocraft

附魔台文字（`galactic`）继续使用未修改的 Monocraft regular TTF，并将 ASCII
字母映射到 Standard Galactic Alphabet。其完整 OFL-1.1 文本保存在
`OFL-Monocraft.txt`。系统/配置字体模式不会加载本目录中的内置字体。

渲染器在字体或任一对应许可证校验失败时回退到系统/配置字体，不会阻止插件启动。
