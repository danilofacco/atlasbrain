def enviar_template(numero, template):
    """Envia um template de WhatsApp pela API oficial da Meta."""
    return {"to": numero, "template": template}
