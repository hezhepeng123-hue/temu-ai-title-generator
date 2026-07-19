# AI 商品标题生成器

Windows 图形界面 APP：选择同一商品的图片，填写已经确认的材质、品牌、件数和尺寸，生成适用于 TEMU、Amazon、TikTok Shop 或 eBay 的商品标题。

## 功能

- 一次选择 1–6 张同一商品图片
- 中文、英文或中英双语标题
- 可填写品类、材质、品牌、包装数量、尺寸和其他确定信息
- 生成多条不同表达的标题
- 复制单条、复制全部、导出 CSV
- API 密钥只在当前运行期间使用，不写入软件或导出文件

## 下载 Windows APP

打开仓库顶部的 **Actions**，进入最新的 **Build Windows APP** 运行记录，在页面底部下载：

`AI_Title_Generator_Windows`

解压后双击 `AI_Title_Generator.exe` 即可使用，不需要安装 Python。

## 使用条件

- Windows 10/11 64 位
- 可联网
- 有效的 OpenAI API Key 和可用 API 额度

软件默认使用 `gpt-5.6` 和 `high` 图片精度。图片会先在电脑中缩小并压缩，再发送给 OpenAI API。

## 重要提醒

AI 不能仅凭图片可靠判断材质、宝石、成分、尺寸、数量、认证和品牌真实性。这些信息应人工确认后填写，上架前也应检查产品事实、知识产权风险和平台规则。

## 本地运行源码

```bash
python -m pip install -r requirements.txt
python temu_ai_title_generator.py
```

## 自动构建

`.github/workflows/build-windows.yml` 会在 Windows 环境中使用 PyInstaller 生成单文件 APP。仓库中不包含任何 API 密钥。
