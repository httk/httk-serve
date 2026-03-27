def execute(material_id, global_data, **kwargs):
    for row in global_data.get("materials", []):
        if row.get("id") == material_id:
            return row
    return None
