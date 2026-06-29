import unittest

import app


FILE = {
    "id": "chat-file",
    "name": "group.txt",
    "path": "Knowledge/chat history/group.txt",
}


class MentorChatIndexingTests(unittest.TestCase):
    def test_only_mentor_messages_become_evidence(self) -> None:
        content = """2025-09-22 10:00:00 'Alice'
为什么价格会上涨？

2025-09-22 10:01:00 'Brind'
先看供需，再看供给是否有弹性。

2025-09-22 10:01:10 'Brind'
短期供给不动，价格反应就会更明显。

2025-09-22 10:02:00 'Bob'
明白了。
"""

        chunks = app.split_into_chunks(FILE, content)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["sourceType"], "mentor_chat")
        self.assertIn("先看供需", chunks[0]["content"])
        self.assertNotIn("为什么价格会上涨", chunks[0]["content"])
        self.assertIn("为什么价格会上涨", chunks[0]["chatContext"])
        self.assertNotIn("明白了", chunks[0]["chatContext"])

    def test_configured_default_chinese_alias_is_mentor(self) -> None:
        self.assertTrue(app.is_mentor_speaker("张成熙"))
        self.assertTrue(app.is_mentor_speaker("Brind"))
        self.assertFalse(app.is_mentor_speaker("Alice"))

    def test_media_only_mentor_message_is_not_indexed(self) -> None:
        content = """2025-09-22 10:00:00 'Alice'
看看这个。

2025-09-22 10:01:00 'Brind'
[图片]
"""

        self.assertEqual(app.split_into_chunks(FILE, content), [])

    def test_regular_notes_keep_standard_chunking(self) -> None:
        note_file = {"id": "note", "name": "note.txt", "path": "Knowledge/note.txt"}
        chunks = app.split_into_chunks(note_file, "第一段笔记。\n\n第二段笔记。")

        self.assertEqual(len(chunks), 1)
        self.assertNotIn("sourceType", chunks[0])


if __name__ == "__main__":
    unittest.main()
