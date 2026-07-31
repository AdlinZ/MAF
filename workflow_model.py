import json
from copy import deepcopy
from pathlib import Path


WORKFLOW_TYPES = (
    "等待",
    "鼠标点击",
    "按键",
    "按键保持",
    "发送指令",
    "截图",
    "等待字幕",
    "OCR 分支",
)

WORKFLOW_PRESETS = {
    "等待": ("1", "", "", "参数 1：等待秒数，例如 2.5"),
    "鼠标点击": ("left", "1", "0.1", "参数：left/right、次数、间隔秒数"),
    "按键": ("w", "1", "0.1", "参数：按键、次数、间隔秒数"),
    "按键保持": ("w", "2", "", "参数：按键、保持秒数"),
    "发送指令": ("/home", "t", "", "参数：ASCII 指令、聊天键"),
    "截图": ("", "", "", "参数 1：保存目录；留空使用默认截图目录"),
    "等待字幕": ("完成", "30", "停止", "参数：关键词(逗号分隔)、超时秒数、超时后停止/继续"),
    "OCR 分支": ("完成", "1", "", "参数：关键词(逗号分隔)、不匹配时跳过的步骤数"),
}


def parse_keyword_list(value):
    keywords = [part.strip() for part in str(value).split(",") if part.strip()]
    if not keywords:
        raise ValueError("OCR 关键词不能为空")
    return keywords


def build_step(step_type, first="", second="", third=""):
    first, second, third = str(first).strip(), str(second).strip(), str(third).strip()
    if step_type == "等待":
        return {"type": step_type, "seconds": max(0.01, float(first))}
    if step_type == "鼠标点击":
        buttons = {"左键": "left", "右键": "right", "left": "left", "right": "right"}
        key = first.lower()
        if key not in buttons:
            raise ValueError("鼠标按键应为 left、right、左键或右键")
        return {
            "type": step_type,
            "button": buttons[key],
            "count": max(1, int(second or "1")),
            "interval": max(0.03, float(third or "0.1")),
        }
    if step_type == "按键":
        if not first:
            raise ValueError("按键不能为空")
        return {
            "type": step_type,
            "key": first.lower(),
            "count": max(1, int(second or "1")),
            "interval": max(0.03, float(third or "0.1")),
        }
    if step_type == "按键保持":
        if not first:
            raise ValueError("按键不能为空")
        return {"type": step_type, "key": first.lower(), "duration": max(0.01, float(second))}
    if step_type == "发送指令":
        if not first or not first.isascii():
            raise ValueError("指令必须是非空 ASCII 文本")
        return {"type": step_type, "text": first, "chat_key": (second or "t").lower()}
    if step_type == "截图":
        return {"type": step_type, "directory": first}
    if step_type == "等待字幕":
        action = third or "停止"
        if action not in ("停止", "继续"):
            raise ValueError("超时动作必须是“停止”或“继续”")
        return {
            "type": step_type,
            "keywords": parse_keyword_list(first),
            "timeout": max(0.1, float(second or "30")),
            "on_timeout": action,
        }
    if step_type == "OCR 分支":
        return {
            "type": step_type,
            "keywords": parse_keyword_list(first),
            "skip": max(1, int(second or "1")),
        }
    raise ValueError(f"未知步骤类型：{step_type}")


def normalize_step(step):
    if not isinstance(step, dict):
        raise ValueError("工作流步骤必须是对象")
    step_type = step.get("type")
    if step_type == "等待":
        return build_step(step_type, step.get("seconds", ""))
    if step_type == "鼠标点击":
        return build_step(step_type, step.get("button", ""), step.get("count", 1), step.get("interval", 0.1))
    if step_type == "按键":
        return build_step(step_type, step.get("key", ""), step.get("count", 1), step.get("interval", 0.1))
    if step_type == "按键保持":
        return build_step(step_type, step.get("key", ""), step.get("duration", ""))
    if step_type == "发送指令":
        return build_step(step_type, step.get("text", ""), step.get("chat_key", "t"))
    if step_type == "截图":
        return build_step(step_type, step.get("directory", ""))
    if step_type == "等待字幕":
        return build_step(
            step_type,
            ",".join(step.get("keywords", [])),
            step.get("timeout", 30),
            step.get("on_timeout", "停止"),
        )
    if step_type == "OCR 分支":
        return build_step(step_type, ",".join(step.get("keywords", [])), step.get("skip", 1))
    raise ValueError(f"未知步骤类型：{step_type}")


def normalize_steps(steps):
    if not isinstance(steps, list) or not steps:
        raise ValueError("工作流至少需要一个步骤")
    return [normalize_step(step) for step in steps]


def step_to_parameters(step):
    """Convert a normalized step back into the three editor fields."""
    step = normalize_step(step)
    step_type = step["type"]
    if step_type == "等待":
        return step_type, f"{step['seconds']:g}", "", ""
    if step_type in ("鼠标点击", "按键"):
        first = step.get("button", step.get("key", ""))
        return step_type, first, str(step["count"]), f"{step['interval']:g}"
    if step_type == "按键保持":
        return step_type, step["key"], f"{step['duration']:g}", ""
    if step_type == "发送指令":
        return step_type, step["text"], step.get("chat_key", "t"), ""
    if step_type == "截图":
        return step_type, step.get("directory", ""), "", ""
    if step_type == "等待字幕":
        return step_type, ",".join(step["keywords"]), f"{step['timeout']:g}", step["on_timeout"]
    if step_type == "OCR 分支":
        return step_type, ",".join(step["keywords"]), str(step["skip"]), ""
    raise ValueError(f"未知步骤类型：{step_type}")


def describe_workflow_step(step):
    step_type = step.get("type", "")
    if step_type == "等待":
        return f"等待 {step['seconds']:g} 秒"
    if step_type == "鼠标点击":
        name = "左键" if step["button"] == "left" else "右键"
        return f"{name}点击 {step['count']} 次，间隔 {step['interval']:g} 秒"
    if step_type == "按键":
        return f"按 {step['key'].upper()} {step['count']} 次，间隔 {step['interval']:g} 秒"
    if step_type == "按键保持":
        return f"保持 {step['key'].upper()} {step['duration']:g} 秒"
    if step_type == "发送指令":
        return f"发送 {step['text']}"
    if step_type == "截图":
        return f"截图到 {step.get('directory') or '默认目录'}"
    if step_type == "等待字幕":
        words = ", ".join(step["keywords"])
        return f"等待字幕 [{words}]，{step['timeout']:g} 秒后{step['on_timeout']}"
    if step_type == "OCR 分支":
        words = ", ".join(step["keywords"])
        return f"若 OCR 不含 [{words}]，跳过后续 {step['skip']} 步"
    return step_type


def workflow_document(name, steps, loops=1, start_delay=5):
    return {
        "format": "minecraft-assistant-workflow",
        "version": 1,
        "name": str(name).strip() or "未命名工作流",
        "steps": normalize_steps(deepcopy(steps)),
        "loops": max(0, int(loops)),
        "start_delay": max(0, float(start_delay)),
    }


def normalize_workflow_document(data):
    if isinstance(data, list):
        data = {"name": "导入的工作流", "steps": data, "loops": 1, "start_delay": 5}
    if not isinstance(data, dict):
        raise ValueError("工作流 JSON 必须是对象或步骤数组")
    return workflow_document(
        data.get("name", "导入的工作流"),
        data.get("steps", []),
        data.get("loops", 1),
        data.get("start_delay", 5),
    )


def load_workflow_file(path):
    return normalize_workflow_document(json.loads(Path(path).read_text(encoding="utf-8")))


def save_workflow_file(path, document):
    normalized = normalize_workflow_document(document)
    Path(path).write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized
