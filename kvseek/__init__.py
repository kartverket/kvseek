# -*- coding: utf-8 -*-

def classFactory(iface):
    from .plugin_kvseek import KvSeekPlugin
    return KvSeekPlugin(iface)
