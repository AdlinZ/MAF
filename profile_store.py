from copy import deepcopy


PROFILE_SECTIONS = (
    "fishing", "clicker", "key_hold", "commands", "workflow", "journeymap", "tools", "system",
)


def normalize_profile(data):
    if not isinstance(data, dict):
        raise ValueError("配置档案必须是对象。")
    return {section: deepcopy(data.get(section, {})) for section in PROFILE_SECTIONS}


def normalize_profiles(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    for name, profile in data.items():
        clean_name = str(name).strip()
        if clean_name and isinstance(profile, dict):
            result[clean_name] = normalize_profile(profile)
    return result
