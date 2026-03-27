def execute(q, global_data, **kwargs):
    needle = str(q).strip().lower()
    if not needle:
        return []

    materials = global_data.get("materials", [])
    out = []
    for row in materials:
        text = " ".join([str(row.get("id", "")), str(row.get("formula", "")), str(row.get("name", ""))]).lower()
        if needle in text:
            out.append(row)
    return out
