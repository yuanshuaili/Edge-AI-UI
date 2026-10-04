import unittest

from ui_backend.config import BackendConfig


def profile():
    return BackendConfig(
        id="sample", name="示例模型", device="边缘设备", mode="Local",
        adapter="mock", endpoint="mock://local",
        capabilities={"text": True, "voice": False, "image": False, "video": False, "tts": False},
        available_inputs={"text": True, "voice": False, "image": False, "video": False},
        runtime_model_name=None,
    )


class AdapterContractTests(unittest.TestCase):
    def test_example_media_adapter_transfers_bytes_not_local_path(self):
        from examples.custom_media_adapter import CustomMediaAdapter
        from ui_backend.media import Attachment, MediaRequest
        from pathlib import Path
        from tempfile import TemporaryDirectory
        received=[]
        def transport(kind,text,mime,chunks):
            received.append((kind,text,mime,b"".join(chunks))); return "远端已收到"
        with TemporaryDirectory() as directory:
            path=Path(directory)/"image"; path.write_bytes(b"fixture")
            adapter=CustomMediaAdapter(profile(),transport)
            reply=adapter.image(MediaRequest("描述",Attachment("id","image","image/png",7,path)))
        self.assertEqual(reply,"远端已收到")
        self.assertEqual(received,[("image","描述","image/png",b"fixture")])
        self.assertNotIn("local_path",adapter.metadata)
    def test_missing_required_methods_cannot_be_instantiated(self):
        from ui_backend.adapter_base import BackendAdapter

        with self.assertRaises(TypeError):
            BackendAdapter(profile())

        class MissingChat(BackendAdapter):
            def health(self):
                return None

        with self.assertRaises(TypeError):
            MissingChat(profile())

    def test_public_metadata_omits_transport_and_runtime_facts(self):
        from ui_backend.adapter_base import BackendAdapter, HealthStatus

        class Example(BackendAdapter):
            def health(self):
                return HealthStatus("ready")

            def text_chat(self, text):
                return text

        adapter = Example(profile())
        self.assertEqual(adapter.id, "sample")
        self.assertEqual(adapter.metadata, {
            "name": "示例模型", "device": "边缘设备", "mode": "Local",
        })
        self.assertNotIn("endpoint", adapter.metadata)
        self.assertNotIn("runtime_model_name", adapter.metadata)
        self.assertTrue(adapter.capabilities["text"])
        self.assertEqual(adapter.text_chat("你好"), "你好")

    def test_unimplemented_optional_methods_fail_closed(self):
        from ui_backend.adapter_base import BackendAdapter, HealthStatus, UnsupportedCapability

        class Example(BackendAdapter):
            def health(self):
                return HealthStatus("ready")

            def text_chat(self, text):
                return text

        adapter = Example(profile())
        for method in (adapter.stream_chat, adapter.image, adapter.video, adapter.voice):
            with self.subTest(method=method.__name__), self.assertRaises(UnsupportedCapability):
                method("payload")
        with self.assertRaises(UnsupportedCapability):
            adapter.clear_session()

    def test_mock_response_identifies_selected_backend_without_loading_model(self):
        from ui_backend.adapters import MockAdapter

        adapter = MockAdapter(profile())
        self.assertEqual(adapter.health().state, "ready")
        self.assertEqual(adapter.text_chat("你好"), "演示后端[sample]已收到：你好")
        self.assertEqual(adapter.text_chat("你好"), "演示后端[sample]已收到：你好")

    def test_registry_only_builds_explicitly_registered_adapters(self):
        from dataclasses import replace
        from ui_backend.adapter_registry import build_adapter, registered_adapter_kinds
        from ui_backend.adapters import MockAdapter

        self.assertIn("mock", registered_adapter_kinds())
        self.assertIsInstance(build_adapter(profile()), MockAdapter)
        with self.assertRaises(ValueError):
            build_adapter(replace(profile(), adapter="some.module:Exploit"))


if __name__ == "__main__":
    unittest.main()
