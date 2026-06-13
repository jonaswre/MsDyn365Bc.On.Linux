import unittest

from parity.collect_contract import (
    http_class,
    normalize_company_name,
    parse_dev_api_major,
    summarize_test_output,
)


class CollectContractTests(unittest.TestCase):
    def test_http_class_normalizes_status_codes(self):
        self.assertEqual("2xx", http_class(200))
        self.assertEqual("3xx", http_class(302))
        self.assertEqual("4xx", http_class(401))
        self.assertEqual("5xx", http_class(503))
        self.assertEqual("000", http_class(0))

    def test_company_name_strips_whitespace(self):
        self.assertEqual("CRONUS International Ltd.", normalize_company_name("  CRONUS International Ltd.  "))

    def test_dev_api_major_reads_metadata_key(self):
        metadata = {"supportedVersions": [{"apiVersion": "7.0"}, {"apiVersion": "6.0"}]}
        self.assertEqual(7, parse_dev_api_major(metadata))

    def test_dev_api_major_returns_none_for_missing_shape(self):
        self.assertIsNone(parse_dev_api_major({"value": []}))

    def test_summarize_test_output_parses_current_run_tests_format(self):
        output = "Test codeunits: 70000,70001\ntotal=4 passed=4 failed=0 skipped=0\n"
        summary = summarize_test_output(output, "websocket")
        self.assertEqual(2, summary["testCodeunitCount"])
        self.assertEqual(4, summary["total"])
        self.assertEqual(4, summary["passed"])
        self.assertEqual(0, summary["failed"])
        self.assertEqual(0, summary["skipped"])
        self.assertEqual("websocket", summary["runnerKind"])


if __name__ == "__main__":
    unittest.main()
