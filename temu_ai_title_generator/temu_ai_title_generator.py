from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import threading
from pathlib import Path
from tkinter import END, BOTH, LEFT, RIGHT, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk
from pydantic import BaseModel, Field


APP_TITLE = "AI商品标题生成器（火山方舟版）"
APP_VERSION = "1.2.0"
DEFAULT_MODEL = "doubao-seed-2-0-pro-260215"
VOLCANO_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
MAX_IMAGES = 6
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class TitleItem(BaseModel):
    language: str = Field(description="中文或English")
    title: str


class TitleResult(BaseModel):
    product_name: str
    category: str
    image_description: str = ""
    observed_features: list[str]
    manual_facts_used: list[str]
    search_keywords: list[str] = Field(default_factory=list)
    gift_keywords: list[str] = Field(default_factory=list)
    warnings: list[str]
    titles: list[TitleItem]


SYSTEM_PROMPT = """
You are a senior cross-border ecommerce listing specialist and visual product analyst.

Your job is to inspect the supplied product images and produce accurate, natural, high-converting
product titles for the selected marketplace. Follow these rules strictly:

1. Separate visual observations from confirmed user-provided facts.
2. Never infer material, metal purity, gemstone identity, fabric composition, dimensions,
   certification, brand, package quantity, medical effect, waterproof rating, or authenticity
   from an image alone.
3. You may use a material, brand, size, quantity, or other specification only when the user
   explicitly supplies it as a confirmed fact.
4. Do not invent trademarks, character names, celebrity names, designer names, patents,
   certifications, test results, discounts, shipping promises, or rankings.
5. Avoid repeated keywords, emojis, all caps, misleading superlatives, and unsupported claims.
6. If the image may show a recognizable third-party character, logo, or protected design,
   describe it generically and add a warning instead of naming the IP.
7. Make every title meaningfully different while preserving the same accurate product facts.
8. Describe the product image carefully: product type, visible colors, shape, pattern, design motif,
   style, target audience, likely use scene, and gift occasion. Do not turn uncertain observations
   into confirmed specifications.
9. Suggest marketplace search phrases with strong purchase intent. These are AI keyword suggestions,
   not claims of access to a live TEMU search ranking.
10. Add only relevant gift-intent phrases. Never force an unrelated holiday, recipient, or occasion.
11. Return structured data matching the requested schema.
""".strip()


def _flatten_manual_facts(values: dict[str, str]) -> list[str]:
    labels = {
        "category_hint": "品类",
        "material": "材质",
        "brand": "品牌",
        "package_quantity": "包装数量",
        "size": "尺码/尺寸",
        "other_facts": "其他确定信息",
    }
    return [f"{labels[key]}：{value}" for key, value in values.items() if value.strip()]


def build_user_prompt(settings: dict[str, str | int]) -> str:
    language = str(settings["language"])
    title_count = int(settings["title_count"])
    max_chars = int(settings["max_chars"])
    manual_values = {
        "category_hint": str(settings.get("category_hint", "")),
        "material": str(settings.get("material", "")),
        "brand": str(settings.get("brand", "")),
        "package_quantity": str(settings.get("package_quantity", "")),
        "size": str(settings.get("size", "")),
        "other_facts": str(settings.get("other_facts", "")),
    }
    manual_facts = _flatten_manual_facts(manual_values)
    target_keywords = str(settings.get("target_keywords", "")).strip()

    if language == "中英双语":
        language_rule = f"生成 {title_count} 条中文标题和 {title_count} 条英文标题。"
    elif language == "英文":
        language_rule = f"生成 {title_count} 条英文标题。"
    else:
        language_rule = f"生成 {title_count} 条中文标题。"

    facts_text = "\n".join(f"- {item}" for item in manual_facts) or "- 未提供；只能使用图片中可直接观察到的外观特征"
    keyword_text = target_keywords or "未指定；请根据图片生成高购买意图的搜索关键词建议（不是实时热搜榜单）"

    return f"""
请根据随附的同一商品图片生成跨境电商标题。

平台：{settings['platform']}
输出语言：{language}
{language_rule}
每条标题最多 {max_chars} 个字符（按普通字符近似控制）。

用户确认的信息：
{facts_text}

用户指定的TEMU关键词：
{keyword_text}

标题写法：
- 先详细观察图片，识别核心品类、颜色、造型、图案、设计元素、风格、适用人群和场景。
- 核心品类放在前部，随后自然加入已确认属性、可见设计、适用人群、使用场景和礼物关键词。
- 符合欧美买家的自然搜索习惯，不机械堆砌同义词。
- 每条标题自然融入 2 至 4 个与图片高度相关的高购买意图搜索短语。
- 每条标题自然融入 1 至 2 个真正相关的礼物意图词，例如 Gift for Her、Birthday Gift、
  Anniversary Gift、Valentine's Day Gift、Mother's Day Gift 或 Christmas Gift；根据商品选择，
  不得每条全部堆入，也不得强行加入不相关人群或节日。
- 如果用户指定了TEMU关键词，优先使用其中与图片和确认事实相符的词；不相关的词必须忽略。
- 不要在标题中使用 Hot Search、Trending、Viral、Best Seller 等无法证实的宣传词。
- 标题之间应更换卖点排序和表达角度，但不得改变产品事实。
- 标题中不要出现平台名称。
- 未确认的材质、宝石、成分、数量、品牌、功效和认证不得写入标题。
- 如果图片不足以确定信息，在 warnings 中明确提示需要人工确认的字段。

严格只输出一个有效的 JSON 对象，不要使用 Markdown 代码块，也不要添加 JSON 以外的文字。
JSON 必须使用下面的字段结构：
{{
  "product_name": "商品概括",
  "category": "识别品类",
  "image_description": "对图片中商品外观、设计、颜色、图案、风格、场景和人群的具体描述",
  "observed_features": ["图片中可直接观察到的特征"],
  "manual_facts_used": ["实际采用的用户确认信息"],
  "search_keywords": ["与图片相关的TEMU高购买意图搜索词建议"],
  "gift_keywords": ["与商品相关的礼物意图关键词"],
  "warnings": ["需要人工确认或可能存在的风险"],
  "titles": [{{"language": "中文或English", "title": "标题"}}]
}}
""".strip()


def image_to_data_url(path: Path, max_side: int = 1600, jpeg_quality: int = 88) -> str:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        if getattr(image, "is_animated", False):
            image.seek(0)
        image = image.convert("RGBA")
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        buffer = io.BytesIO()
        background.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("AI返回内容不是有效JSON。")
    return json.loads(cleaned[start : end + 1])


def generate_with_volcengine(
    api_key: str,
    model: str,
    image_paths: list[Path],
    settings: dict[str, str | int],
) -> TitleResult:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("程序组件不完整，请重新下载最新版火山方舟版软件。") from exc

    client = OpenAI(
        api_key=api_key,
        base_url=VOLCANO_BASE_URL,
        timeout=120.0,
        max_retries=2,
    )
    content: list[dict[str, str]] = [
        {"type": "input_text", "text": build_user_prompt(settings)}
    ]
    detail = str(settings.get("detail", "auto"))
    for path in image_paths:
        content.append(
            {
                "type": "input_image",
                "image_url": image_to_data_url(path),
                "detail": detail,
            }
        )

    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        if response.output_text:
            return TitleResult.model_validate(_extract_json(response.output_text))
        raise RuntimeError("AI没有返回标题，请稍后重试。")
    except AttributeError as exc:
        raise RuntimeError("API兼容组件版本过旧，请重新下载最新版软件。") from exc


class TitleGeneratorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1220x820")
        self.root.minsize(1060, 720)

        self.image_paths: list[Path] = []
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.latest_result: TitleResult | None = None

        self.api_key_var = tk.StringVar()
        self.model_var = tk.StringVar(value=DEFAULT_MODEL)
        self.platform_var = tk.StringVar(value="TEMU")
        self.language_var = tk.StringVar(value="中文")
        self.title_count_var = tk.IntVar(value=8)
        self.max_chars_var = tk.IntVar(value=180)
        self.detail_var = tk.StringVar(value="auto")
        self.category_var = tk.StringVar()
        self.material_var = tk.StringVar()
        self.brand_var = tk.StringVar(value="UBERTE")
        self.quantity_var = tk.StringVar()
        self.size_var = tk.StringVar()
        self.keyword_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择1至6张同一商品的图片")
        env_ready = bool(os.getenv("ARK_API_KEY", "").strip())
        self.key_status_var = tk.StringVar(
            value="已检测到火山方舟密钥" if env_ready else "粘贴火山方舟API Key；不会保存"
        )

        self._setup_style()
        self._build_ui()

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=7)
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 18, "bold"), foreground="#17345c")
        style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 9), foreground="#667085")
        style.configure("Warning.TLabel", font=("Microsoft YaHei UI", 9), foreground="#b54708")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 11, "bold"))

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        header.pack(fill=X)
        ttk.Label(header, text="AI商品标题生成器", style="Heading.TLabel").pack(side=LEFT)
        ttk.Label(
            header,
            text="火山方舟图片识别 + 已确认属性 → TEMU/跨境电商标题",
            style="Hint.TLabel",
        ).pack(side=LEFT, padx=16, pady=(7, 0))

        main = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=BOTH, expand=True, padx=16, pady=(0, 12))

        left = ttk.Frame(main, padding=8)
        right = ttk.Frame(main, padding=8)
        main.add(left, weight=1)
        main.add(right, weight=2)

        self._build_left_panel(left)
        self._build_right_panel(right)

        footer = ttk.Frame(self.root, padding=(18, 0, 18, 12))
        footer.pack(fill=X)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=180)
        self.progress.pack(side=LEFT)
        ttk.Label(footer, textvariable=self.status_var, style="Hint.TLabel").pack(side=LEFT, padx=12)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        images_box = ttk.Labelframe(parent, text="1. 商品图片", padding=10)
        images_box.pack(fill=X, pady=(0, 10))
        buttons = ttk.Frame(images_box)
        buttons.pack(fill=X)
        ttk.Button(buttons, text="选择图片", command=self.select_images).pack(side=LEFT)
        ttk.Button(buttons, text="清空", command=self.clear_images).pack(side=LEFT, padx=6)
        ttk.Label(images_box, text="最多6张，必须属于同一商品", style="Hint.TLabel").pack(anchor="w", pady=(6, 4))

        self.file_list = tk.Listbox(images_box, height=5, font=("Microsoft YaHei UI", 9))
        self.file_list.pack(fill=X)
        self.file_list.bind("<<ListboxSelect>>", self._on_file_select)

        self.preview_label = ttk.Label(images_box, text="图片预览", anchor="center")
        self.preview_label.pack(fill=X, pady=(8, 0))

        facts_box = ttk.Labelframe(parent, text="2. 确定的商品信息", padding=10)
        facts_box.pack(fill=BOTH, expand=True)
        self._labeled_entry(facts_box, "品类提示", self.category_var, "例如：戒指、项链、高腰内裤")
        self._labeled_entry(facts_box, "材质", self.material_var, "例如：S925银、锦纶；不确定请留空")
        self._labeled_entry(facts_box, "品牌", self.brand_var, "不想写入标题可留空")
        self._labeled_entry(facts_box, "包装数量", self.quantity_var, "例如：2件装、6款不重复")
        self._labeled_entry(facts_box, "尺码/尺寸", self.size_var, "例如：S-2XL、主石1克拉")
        self._labeled_entry(
            facts_box,
            "指定TEMU关键词（可选）",
            self.keyword_var,
            "可粘贴你查到的真实热搜词；留空则由AI根据图片建议",
        )

        ttk.Label(facts_box, text="其他确定信息").pack(anchor="w", pady=(7, 2))
        self.other_text = tk.Text(facts_box, height=4, wrap="word", font=("Microsoft YaHei UI", 9))
        self.other_text.pack(fill=X)
        ttk.Label(
            facts_box,
            text="重要：材质、宝石、成分、尺寸和数量不能只靠图片判断。",
            style="Warning.TLabel",
            wraplength=360,
        ).pack(anchor="w", pady=(7, 0))

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        config_box = ttk.Labelframe(parent, text="3. 生成设置", padding=10)
        config_box.pack(fill=X, pady=(0, 10))

        row1 = ttk.Frame(config_box)
        row1.pack(fill=X)
        self._combo(row1, "平台", self.platform_var, ["TEMU", "Amazon", "TikTok Shop", "eBay"], 14)
        self._combo(row1, "语言", self.language_var, ["中文", "英文", "中英双语"], 12)
        self._spin(row1, "每种语言数量", self.title_count_var, 1, 20, 7)
        self._spin(row1, "单条最长字符", self.max_chars_var, 40, 300, 8)

        row2 = ttk.Frame(config_box)
        row2.pack(fill=X, pady=(8, 0))
        ttk.Label(row2, text="模型").pack(side=LEFT)
        ttk.Entry(row2, textvariable=self.model_var, width=18).pack(side=LEFT, padx=(5, 16))
        self._combo(row2, "图片精度", self.detail_var, ["auto", "high", "low"], 10)
        ttk.Label(row2, text="火山API Key").pack(side=LEFT, padx=(12, 0))
        ttk.Entry(row2, textvariable=self.api_key_var, show="●", width=28).pack(side=LEFT, padx=5, fill=X, expand=True)
        ttk.Label(row2, textvariable=self.key_status_var, style="Hint.TLabel").pack(side=LEFT, padx=5)

        action_bar = ttk.Frame(parent)
        action_bar.pack(fill=X, pady=(0, 10))
        self.generate_button = ttk.Button(
            action_bar,
            text="开始生成标题",
            command=self.start_generation,
            style="Accent.TButton",
        )
        self.generate_button.pack(side=LEFT)
        ttk.Button(action_bar, text="复制全部", command=self.copy_all).pack(side=LEFT, padx=8)
        ttk.Button(action_bar, text="导出CSV", command=self.export_csv).pack(side=LEFT)
        ttk.Label(
            action_bar,
            text="火山方舟调用可能产生费用；图片会压缩后发送",
            style="Hint.TLabel",
        ).pack(side=RIGHT)

        summary_box = ttk.Labelframe(parent, text="商品识别与提醒", padding=8)
        summary_box.pack(fill=X, pady=(0, 10))
        self.summary_text = tk.Text(
            summary_box,
            height=10,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            state="disabled",
            background="#f8fafc",
        )
        self.summary_text.pack(fill=X)

        result_box = ttk.Labelframe(parent, text="生成结果（双击标题可复制）", padding=8)
        result_box.pack(fill=BOTH, expand=True)
        columns = ("index", "language", "title", "length")
        self.result_tree = ttk.Treeview(result_box, columns=columns, show="headings", height=14)
        self.result_tree.heading("index", text="#")
        self.result_tree.heading("language", text="语言")
        self.result_tree.heading("title", text="标题")
        self.result_tree.heading("length", text="字符数")
        self.result_tree.column("index", width=42, anchor="center", stretch=False)
        self.result_tree.column("language", width=75, anchor="center", stretch=False)
        self.result_tree.column("title", width=650, anchor="w")
        self.result_tree.column("length", width=64, anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(result_box, orient="vertical", command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        self.result_tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.result_tree.bind("<Double-1>", self.copy_selected)

    def _labeled_entry(self, parent, label, variable, hint) -> None:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(6, 2))
        ttk.Entry(parent, textvariable=variable).pack(fill=X)
        ttk.Label(parent, text=hint, style="Hint.TLabel").pack(anchor="w")

    def _combo(self, parent, label, variable, values, width) -> None:
        group = ttk.Frame(parent)
        group.pack(side=LEFT, padx=(0, 14))
        ttk.Label(group, text=label).pack(side=LEFT)
        ttk.Combobox(group, textvariable=variable, values=values, state="readonly", width=width).pack(side=LEFT, padx=5)

    def _spin(self, parent, label, variable, start, end, width) -> None:
        group = ttk.Frame(parent)
        group.pack(side=LEFT, padx=(0, 14))
        ttk.Label(group, text=label).pack(side=LEFT)
        ttk.Spinbox(group, textvariable=variable, from_=start, to=end, width=width).pack(side=LEFT, padx=5)

    def select_images(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择同一商品的图片",
            filetypes=[("商品图片", "*.jpg *.jpeg *.png *.webp *.gif"), ("所有文件", "*.*")],
        )
        if not files:
            return
        selected = [Path(item) for item in files if Path(item).suffix.lower() in SUPPORTED_EXTENSIONS]
        existing = {str(path.resolve()) for path in self.image_paths}
        added_count = 0
        for path in selected:
            resolved = str(path.resolve())
            if resolved not in existing and len(self.image_paths) < MAX_IMAGES:
                self.image_paths.append(path)
                existing.add(resolved)
                added_count += 1
        if added_count < len(selected):
            messagebox.showinfo("图片数量", f"最多使用{MAX_IMAGES}张图片，多余图片未加入。")
        self._refresh_file_list()

    def clear_images(self) -> None:
        self.image_paths.clear()
        self.file_list.delete(0, END)
        self.preview_photo = None
        self.preview_label.configure(image="", text="图片预览")
        self.status_var.set("请选择1至6张同一商品的图片")

    def _refresh_file_list(self) -> None:
        self.file_list.delete(0, END)
        for index, path in enumerate(self.image_paths, start=1):
            self.file_list.insert(END, f"{index}. {path.name}")
        if self.image_paths:
            self.file_list.selection_set(0)
            self._show_preview(self.image_paths[0])
            self.status_var.set(f"已选择 {len(self.image_paths)} 张图片")

    def _on_file_select(self, _event=None) -> None:
        selection = self.file_list.curselection()
        if selection:
            self._show_preview(self.image_paths[selection[0]])

    def _show_preview(self, path: Path) -> None:
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.thumbnail((360, 220), Image.Resampling.LANCZOS)
                self.preview_photo = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self.preview_photo, text="")
        except Exception as exc:
            self.preview_label.configure(image="", text=f"无法预览：{exc}")

    def _settings(self) -> dict[str, str | int]:
        return {
            "platform": self.platform_var.get(),
            "language": self.language_var.get(),
            "title_count": int(self.title_count_var.get()),
            "max_chars": int(self.max_chars_var.get()),
            "detail": self.detail_var.get(),
            "category_hint": self.category_var.get().strip(),
            "material": self.material_var.get().strip(),
            "brand": self.brand_var.get().strip(),
            "package_quantity": self.quantity_var.get().strip(),
            "size": self.size_var.get().strip(),
            "target_keywords": self.keyword_var.get().strip(),
            "other_facts": self.other_text.get("1.0", END).strip(),
        }

    def start_generation(self) -> None:
        if not self.image_paths:
            messagebox.showwarning("缺少图片", "请先选择至少1张商品图片。")
            return
        api_key = self.api_key_var.get().strip() or os.getenv("ARK_API_KEY", "").strip()
        if not api_key:
            messagebox.showwarning("缺少API Key", "请粘贴火山方舟API Key，或设置 ARK_API_KEY。")
            return
        model = self.model_var.get().strip()
        if not model:
            messagebox.showwarning("缺少模型", "请填写模型名称。")
            return

        self.generate_button.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("正在识别图片并生成标题，请稍候……")
        self.latest_result = None
        worker = threading.Thread(
            target=self._generate_worker,
            args=(api_key, model, list(self.image_paths), self._settings()),
            daemon=True,
        )
        worker.start()

    def _generate_worker(self, api_key, model, paths, settings) -> None:
        try:
            result = generate_with_volcengine(api_key, model, paths, settings)
        except Exception as exc:
            self.root.after(0, lambda error=exc: self._generation_failed(error))
            return
        self.root.after(0, lambda: self._generation_succeeded(result))

    def _generation_succeeded(self, result: TitleResult) -> None:
        self.progress.stop()
        self.generate_button.configure(state="normal")
        self.latest_result = result
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        for index, item in enumerate(result.titles, start=1):
            self.result_tree.insert("", END, values=(index, item.language, item.title, len(item.title)))

        summary_lines = [
            f"识别品类：{result.category}",
            f"商品概括：{result.product_name}",
            f"图片描述：{result.image_description or '无'}",
            "可见特征：" + ("、".join(result.observed_features) or "无"),
            "已使用的人工确认信息：" + ("、".join(result.manual_facts_used) or "无"),
            "TEMU搜索词建议（非实时榜单）：" + ("、".join(result.search_keywords) or "无"),
            "礼物关键词：" + ("、".join(result.gift_keywords) or "无"),
            "需要确认：" + ("；".join(result.warnings) or "无"),
        ]
        self._set_summary("\n".join(summary_lines))
        self.status_var.set(f"生成完成：{len(result.titles)} 条标题")

    def _generation_failed(self, exc: Exception) -> None:
        self.progress.stop()
        self.generate_button.configure(state="normal")
        message = str(exc)
        if "401" in message or "api key" in message.lower() or "authentication" in message.lower():
            message = "火山方舟API Key无效或没有权限，请检查密钥和模型权限。"
        elif "403" in message or "model" in message.lower() and "not" in message.lower():
            message = "模型未开通或模型ID不正确，请在火山方舟开通模型后重试。"
        elif "429" in message or "rate" in message.lower() or "quota" in message.lower():
            message = "火山方舟额度不足或请求过快，请检查余额或稍后重试。"
        elif "connection" in message.lower() or "timeout" in message.lower():
            message = "网络连接失败或超时，请检查网络后重试。"
        self.status_var.set("生成失败")
        messagebox.showerror("生成失败", message)

    def _set_summary(self, text: str) -> None:
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", END)
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state="disabled")

    def copy_selected(self, _event=None) -> None:
        selection = self.result_tree.selection()
        if not selection:
            return
        values = self.result_tree.item(selection[0], "values")
        title = values[2]
        self.root.clipboard_clear()
        self.root.clipboard_append(title)
        self.status_var.set("已复制选中标题")

    def copy_all(self) -> None:
        if not self.latest_result:
            messagebox.showinfo("暂无结果", "请先生成标题。")
            return
        text = "\n".join(
            f"{index}. [{item.language}] {item.title}"
            for index, item in enumerate(self.latest_result.titles, start=1)
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("已复制全部标题")

    def export_csv(self) -> None:
        if not self.latest_result:
            messagebox.showinfo("暂无结果", "请先生成标题。")
            return
        filename = filedialog.asksaveasfilename(
            title="导出标题",
            defaultextension=".csv",
            initialfile="AI商品标题.csv",
            filetypes=[("CSV文件", "*.csv")],
        )
        if not filename:
            return
        result = self.latest_result
        with open(filename, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["序号", "平台", "语言", "标题", "字符数", "识别品类", "提醒"])
            warning_text = "；".join(result.warnings)
            for index, item in enumerate(result.titles, start=1):
                writer.writerow(
                    [index, self.platform_var.get(), item.language, item.title, len(item.title), result.category, warning_text]
                )
        self.status_var.set(f"已导出：{filename}")
        messagebox.showinfo("导出成功", "标题CSV已保存，可直接用Excel打开。")


def main() -> None:
    root = tk.Tk()
    app = TitleGeneratorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
