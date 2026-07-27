"""NapCat 适配器 v4 — QQ 平台消息收发适配器。"""
from .plugin import NapCatAdapterPlugin

def create_plugin() -> type[NapCatAdapterPlugin]:
    return NapCatAdapterPlugin
