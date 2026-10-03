"""Small serial model-server control protocol; imports no model libraries."""


class ConversationState:
    """Own prompt history and cache position, not model/tokenizer/weights.

    Called only by the serial server loop, after any preceding generation.
    At position zero inference overwrites and reads only the new cache prefix;
    old cache allocation can be reused without freeing or reloading weights.
    """

    def __init__(self, prompter):
        self.prompter = prompter
        self.initial_template = prompter.template
        self.start_pos = 0

    def control(self, request):
        if not isinstance(request, dict):
            return {"ok": False, "error": "Invalid request"}
        if "action" not in request:
            return None  # Preserve the original {text: ...} protocol.
        if request.get("action") != "clear_session" or set(request) != {"action"}:
            return {"ok": False, "error": "Unsupported action"}
        self.prompter.template = self.initial_template
        self.prompter.model_input = None
        self.start_pos = 0
        return {"ok": True, "action": "clear_session"}
