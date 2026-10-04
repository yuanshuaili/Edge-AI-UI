"use strict";

// One native File + one object URL; never copy video into base64 or chat history.
class MediaComposer {
  constructor({onChange, onError, limits}) {
    this.onChange = onChange; this.onError = onError; this.limits = limits;
    this._pending = null; this._file = null; this._url = null; this._xhr = null; this._sequence = 0;
    this.preview = document.getElementById("attachmentPreview");
    this.content = document.getElementById("attachmentContent");
    this.progress = document.getElementById("attachmentProgress");
    this.input = document.getElementById("attachmentInput");
    this._inputListener = () => {
      const file = this.input.files[0]; this.input.value = "";
      if (file) this.upload(file, this.kind, this.backendId).catch(error => {
        if (error.name !== "AbortError") this.onError(error.message);
      });
    };
    this.input.addEventListener("change", this._inputListener);
    document.getElementById("attachmentRemove").addEventListener("click", () => this.clear());
  }
  get pending() { return this._pending; }
  get uploading() { return this._xhr !== null; }
  get hasFile() { return this._file !== null; }
  get name() { return this._file?.name || ""; }
  choose(kind, backendId) {
    this.kind = kind; this.backendId = backendId;
    this.input.accept = kind === "image" ? "image/jpeg,image/png,image/webp" : "video/mp4,video/webm";
    this.input.click();
  }
  _discard(metadata) {
    if (metadata?.attachment_id) fetch(`/api/attachments/${encodeURIComponent(metadata.attachment_id)}`,
      {method: "DELETE", keepalive: true}).catch(() => {});
  }
  clear({discard = true} = {}) {
    this._sequence++;
    const xhr = this._xhr; this._xhr = null; xhr?.abort();
    if (discard) this._discard(this._pending);
    this._pending = null; this._file = null;
    if (this._url) URL.revokeObjectURL(this._url);
    this._url = null;
    this.content.replaceChildren(); this.preview.hidden = true; this.progress.textContent = "";
    this.onChange();
  }
  upload(file, kind, backendId) {
    const types = kind === "image" ? ["image/jpeg", "image/png", "image/webp"] : ["video/mp4", "video/webm"];
    if (!["image", "video"].includes(kind) || !types.includes(file.type)) return Promise.reject(new Error("请选择 JPEG、PNG、WebP 图片或 MP4、WebM 视频。"));
    if (!file.size || file.size > this.limits[`${kind}_bytes`]) return Promise.reject(new Error("附件大小超过当前限制。"));
    this.clear();
    const sequence = this._sequence;
    this._file = file; this.kind = kind; this.backendId = backendId;
    this._url = URL.createObjectURL(file);
    const media = document.createElement(kind === "image" ? "img" : "video");
    media.src = this._url;
    if (kind === "video") { media.controls = true; media.preload = "metadata"; }
    else media.alt = "待发送图片预览";
    const name = document.createElement("span"); name.textContent = file.name;
    this.content.replaceChildren(media, name); this.preview.hidden = false;
    const xhr = new XMLHttpRequest(); this._xhr = xhr;
    this.progress.textContent = "正在上传…"; this.onChange();
    return new Promise((resolve, reject) => {
      xhr.open("POST", `/api/attachments?kind=${kind}&backend_id=${encodeURIComponent(backendId)}`);
      xhr.setRequestHeader("Content-Type", file.type);
      xhr.timeout = ((this.limits.upload_seconds || 30) + 5) * 1000;
      xhr.upload.onprogress = event => {
        if (sequence === this._sequence && event.lengthComputable) this.progress.textContent = `上传 ${Math.round(event.loaded / event.total * 100)}%`;
      };
      const fail = error => {
        if (sequence === this._sequence) { this._xhr = null; this.clear(); }
        reject(error);
      };
      xhr.onload = () => {
        let result;
        try { result = JSON.parse(xhr.responseText); } catch { fail(new Error("上传未完成，请重试。")); return; }
        if (sequence !== this._sequence) { this._discard(result); reject(new DOMException("上传已取消", "AbortError")); return; }
        if (xhr.status !== 201 || !result.attachment_id) { fail(new Error(result.error?.message || "附件上传失败。")); return; }
        this._xhr = null; this._pending = result; this.progress.textContent = "附件已就绪"; this.onChange(); resolve(result);
      };
      xhr.onerror = () => fail(new Error("附件上传失败，请重试。"));
      xhr.ontimeout = () => fail(new Error("附件上传超时，请重试。"));
      xhr.onabort = () => fail(new DOMException("上传已取消", "AbortError"));
      xhr.send(file);
    });
  }
  dispose() { this.clear(); this.input.removeEventListener("change", this._inputListener); }
}
window.MediaComposer = MediaComposer;
