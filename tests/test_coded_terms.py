import unittest

import app


def chunk(chunk_id: str, content: str) -> dict:
    return {
        "id": chunk_id,
        "title": "notes.txt",
        "source": "Knowledge/notes.txt",
        "content": content,
    }


class CodedTermTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_groups = app.CODED_TERM_GROUPS
        app.CODED_TERM_GROUPS = {
            "正式名称": ("正式名称", "代称甲"),
            "Organization": ("Organization", "AliasOne", "AliasTwo"),
        }

    def tearDown(self) -> None:
        app.CODED_TERM_GROUPS = self.original_groups

    def test_formal_name_retrieves_coded_term(self) -> None:
        ranked = app.rough_rank_chunks(
            "正式名称所代表的概念如何理解？",
            [chunk("coded", "这里使用代称甲讨论相关概念。"), chunk("other", "这是无关内容。")],
        )

        self.assertEqual([item["id"] for item in ranked], ["coded"])

    def test_coded_term_retrieves_formal_name(self) -> None:
        ranked = app.rough_rank_chunks(
            "代称甲是什么意思？",
            [chunk("formal", "这段讨论正式名称。"), chunk("other", "这是无关内容。")],
        )

        self.assertEqual([item["id"] for item in ranked], ["formal"])

    def test_ascii_aliases_are_case_insensitive_equivalents(self) -> None:
        terms = app.expanded_retrieval_terms("aliasone structure")

        self.assertIn("aliastwo", [term.casefold() for term in terms])
        self.assertIn("organization", [term.casefold() for term in terms])

    def test_prompt_receives_only_relevant_equivalence_guidance(self) -> None:
        guidance = app.coded_term_guidance("AliasTwo structure")

        self.assertIn("Organization", guidance)
        self.assertIn("AliasOne", guidance)
        self.assertNotIn("正式名称", guidance)


class SourcePriorityTests(unittest.TestCase):
    def test_candidate_pool_reserves_document_and_chat_quotas(self) -> None:
        documents = [chunk(f"doc-{index}", "共同主题") for index in range(25)]
        chats = [
            {**chunk(f"chat-{index}", "共同主题"), "sourceType": "mentor_chat"}
            for index in range(40)
        ]

        ranked = app.rough_rank_chunks("共同主题", [*chats, *documents], limit=30)

        self.assertEqual(sum(item.get("sourceType") != "mentor_chat" for item in ranked), 20)
        self.assertEqual(sum(item.get("sourceType") == "mentor_chat" for item in ranked), 10)

    def test_final_sources_cap_chat_when_documents_exist(self) -> None:
        documents = [chunk(f"doc-{index}", "内容") for index in range(5)]
        chats = [
            {**chunk(f"chat-{index}", "内容"), "sourceType": "mentor_chat"}
            for index in range(8)
        ]

        selected = app.prioritize_selected_sources([*chats, *documents], limit=8)

        self.assertEqual(sum(item.get("sourceType") != "mentor_chat" for item in selected), 5)
        self.assertEqual(sum(item.get("sourceType") == "mentor_chat" for item in selected), 3)

    def test_chat_can_fill_sources_when_no_document_is_relevant(self) -> None:
        chats = [
            {**chunk(f"chat-{index}", "内容"), "sourceType": "mentor_chat"}
            for index in range(8)
        ]

        self.assertEqual(len(app.prioritize_selected_sources(chats, limit=8)), 8)


if __name__ == "__main__":
    unittest.main()
