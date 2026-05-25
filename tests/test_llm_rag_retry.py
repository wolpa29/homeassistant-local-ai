import unittest
from unittest.mock import patch

from core.llm import _clean_history_for_llm, _validate_actions, format_history_block_for_llm, parse_command_rag


class _Response:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class RagParserRetryTest(unittest.TestCase):
    def setUp(self):
        self.entities = [
            {
                "entity_id": "automation.trigger_pool_pump_off",
                "friendly_name": "Pool Pumpe aus",
                "domain": "automation",
                "actions": ["trigger"],
                "meta": "",
            }
        ]

    @patch("core.ha.get_states_bulk", return_value={})
    @patch("core.llm.requests.post")
    def test_retries_when_model_echoes_execution_summary(self, post, _states):
        post.side_effect = [
            _Response(
                "Die Pool Pumpe schalte ich aus.\n"
                "ausgefuehrt: trigger -> automation.trigger_pool_pump_off [OK]"
            ),
            _Response(
                '{"reply":"Ich schalte die Pool Pumpe aus.",'
                '"actions":[{"entity_id":"automation.trigger_pool_pump_off",'
                '"action":"trigger","domain":"automation"}]}'
            ),
        ]

        result = parse_command_rag("Schalt die pumpe wieder aus", self.entities)

        self.assertEqual(2, post.call_count)
        self.assertEqual(
            [{"entity_id": "automation.trigger_pool_pump_off", "action": "trigger", "domain": "automation"}],
            result["actions"],
        )

    @patch("core.ha.get_states_bulk", return_value={})
    @patch("core.llm.requests.post")
    def test_rejects_execution_summary_when_retry_is_not_json(self, post, _states):
        post.side_effect = [
            _Response(
                "Die Pool Pumpe schalte ich aus.\n"
                "ausgefuehrt: trigger -> automation.trigger_pool_pump_off [OK]"
            ),
            _Response("ausgefuehrt: trigger -> automation.trigger_pool_pump_off [OK]"),
        ]

        self.assertIsNone(parse_command_rag("Schalt die pumpe wieder aus", self.entities))
        self.assertEqual(2, post.call_count)

    def test_history_rewrites_execution_summary_as_context(self):
        history = [
            {"role": "user", "content": "Schalt die Pool Pumpe ein"},
            {
                "role": "assistant",
                "content": (
                    '{"reply":"Ich schalte die Pool Pumpe ein.",'
                    '"actions":[{"entity_id":"automation.trigger_pool_pump_on",'
                    '"action":"trigger","domain":"automation"}]}\n'
                    "ausgefuehrt: trigger -> automation.trigger_pool_pump_on [OK]"
                ),
            },
        ]

        cleaned = _clean_history_for_llm(history)

        self.assertEqual("Schalt die Pool Pumpe ein", cleaned[0]["content"])
        self.assertIn(
            "Ausfuehrungskontext: erfolgreich trigger auf automation.trigger_pool_pump_on",
            cleaned[1]["content"],
        )
        self.assertNotIn("ausgefuehrt:", cleaned[1]["content"])
        self.assertNotIn("[OK]", cleaned[1]["content"])

    def test_retry_history_can_strip_execution_summary(self):
        history = [
            {"role": "user", "content": "Schalt die Pool Pumpe ein"},
            {
                "role": "assistant",
                "content": (
                    '{"reply":"Ich schalte die Pool Pumpe ein.",'
                    '"actions":[{"entity_id":"automation.trigger_pool_pump_on",'
                    '"action":"trigger","domain":"automation"}]}\n'
                    "ausgefuehrt: trigger -> automation.trigger_pool_pump_on [OK]"
                ),
            },
        ]

        cleaned = _clean_history_for_llm(history, include_execution_summaries=False)

        self.assertEqual("Schalt die Pool Pumpe ein", cleaned[0]["content"])
        self.assertEqual("Ich schalte die Pool Pumpe ein.", cleaned[1]["content"])

    def test_history_rewrites_multiple_execution_summaries(self):
        history = [
            {
                "role": "assistant",
                "content": (
                    '{"reply":"Ich schalte beides.","actions":[]}\n'
                    "ausgefuehrt: turn_on -> light.kueche_decke [OK], "
                    "turn_on -> switch.rollo_1_essen_ab [OK]"
                ),
            },
        ]

        cleaned = _clean_history_for_llm(history)

        self.assertIn(
            "Ausfuehrungskontext: erfolgreich turn_on auf light.kueche_decke; "
            "erfolgreich turn_on auf switch.rollo_1_essen_ab",
            cleaned[0]["content"],
        )
        self.assertNotIn("ausgefuehrt:", cleaned[0]["content"])
        self.assertNotIn("[OK]", cleaned[0]["content"])

    def test_history_rewrites_error_and_timeout_status(self):
        history = [
            {
                "role": "assistant",
                "content": (
                    '{"reply":"Ich versuche es.","actions":[]}\n'
                    "ausgefuehrt: turn_off -> light.kueche_decke [FEHLER], "
                    "trigger -> automation.trigger_pool_pump_off "
                    "[Timeout - moeglicherweise ausgefuehrt]"
                ),
            },
        ]

        cleaned = _clean_history_for_llm(history)

        self.assertIn("fehlgeschlagen turn_off auf light.kueche_decke", cleaned[0]["content"])
        self.assertIn(
            "Timeout, Ausfuehrung unbekannt trigger auf automation.trigger_pool_pump_off",
            cleaned[0]["content"],
        )
        self.assertNotIn("[FEHLER]", cleaned[0]["content"])
        self.assertNotIn("[Timeout - moeglicherweise ausgefuehrt]", cleaned[0]["content"])

    def test_action_validation_rejects_action_not_allowed_for_entity(self):
        result = {
            "actions": [
                {"entity_id": "automation.trigger_pool_pump_off", "action": "turn_on", "domain": "automation"},
                {"entity_id": "automation.trigger_pool_pump_off", "action": "trigger", "domain": "automation"},
            ]
        }

        validated = _validate_actions(
            result,
            {"automation.trigger_pool_pump_off"},
            {"automation.trigger_pool_pump_off": {"trigger"}},
            "TEST",
        )

        self.assertEqual(
            [{"entity_id": "automation.trigger_pool_pump_off", "action": "trigger", "domain": "automation"}],
            validated,
        )

    def test_rewriter_history_block_uses_same_sanitized_context(self):
        history = [
            {"role": "user", "content": "Schalt die Pool Pumpe aus"},
            {
                "role": "assistant",
                "content": (
                    '{"reply":"Ich schalte die Pool Pumpe aus.",'
                    '"actions":[{"entity_id":"automation.trigger_pool_pump_off",'
                    '"action":"trigger","domain":"automation"}]}\n'
                    "ausgefuehrt: trigger -> automation.trigger_pool_pump_off [OK]"
                ),
            },
        ]

        block = format_history_block_for_llm(history)

        self.assertIn("Nutzer: Schalt die Pool Pumpe aus", block)
        self.assertIn("Assistent: Ich schalte die Pool Pumpe aus.", block)
        self.assertIn(
            "Ausfuehrungskontext: erfolgreich trigger auf automation.trigger_pool_pump_off",
            block,
        )
        self.assertNotIn("ausgefuehrt:", block)
        self.assertNotIn("[OK]", block)


if __name__ == "__main__":
    unittest.main()
