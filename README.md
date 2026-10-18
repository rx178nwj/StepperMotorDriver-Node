# StepperMotorDriver-Node

NEMA17 用 / NEMA23 用のステッピングモータードライバー基板と、そのファームウェアのリポジトリです。
基板をモーター背面に取り付け、CAN バス経由で制御するノードとして使います。

## ディレクトリ構成

| パス | 内容 |
|------|------|
| `schematic_NEMA17/` | NEMA17 用基板の KiCad プロジェクト（回路図・基板・Gerber など） |
| `schematic_NEMA23/` | NEMA23 用基板の KiCad プロジェクト（回路図・基板・Gerber など） |
| `firmware/` | ファームウェア（STM32L433、CMake ベース。詳細は `firmware/README.md`） |
| `docs/` | ドキュメント |
| `datasheet/` | 部品データシート置き場（本体は含まない。`datasheet/README.md` 参照） |
