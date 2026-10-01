from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
import sys
import json
import importlib.util
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "shangan_schedule.py"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class ValidateCliTests(unittest.TestCase):
    def write_mutated_fixture(self, directory: str, mutate) -> Path:
        payload = json.loads(
            (FIXTURES / "candidate-review-mixed.json").read_text(encoding="utf-8")
        )
        mutate(payload)
        output = Path(directory) / "candidate.json"
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return output

    def test_validate_rejects_source_outside_official_whitelist(self) -> None:
        result = run_cli(
            "validate",
            "--input",
            str(FIXTURES / "candidate-unofficial-source.json"),
            "--sources",
            str(FIXTURES / "official-sources.json"),
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("来源域名不在官方白名单", result.stderr)
        self.assertIn("example.com", result.stderr)

    def test_validate_rejects_impossible_date(self) -> None:
        result = run_cli(
            "validate",
            "--input",
            str(FIXTURES / "candidate-invalid-date.json"),
            "--sources",
            str(FIXTURES / "official-sources.json"),
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("不是有效日期", result.stderr)
        self.assertIn("2025-02-30", result.stderr)

    def test_validate_requires_source_evidence(self) -> None:
        result = run_cli(
            "validate",
            "--input",
            str(FIXTURES / "candidate-missing-evidence.json"),
            "--sources",
            str(FIXTURES / "official-sources.json"),
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("缺少来源证据", result.stderr)

    def test_validate_rejects_duplicate_event_ids(self) -> None:
        result = run_cli(
            "validate",
            "--input",
            str(FIXTURES / "candidate-duplicate-events.json"),
            "--sources",
            str(FIXTURES / "official-sources.json"),
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("事件 ID 重复", result.stderr)
        self.assertIn("cn-national-2026-registration", result.stderr)

    def test_validate_rejects_end_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0]["events"][0].update(
                    {"start": "2025-10-24", "end": "2025-10-15"}
                ),
            )
            result = run_cli(
                "validate",
                "--input",
                str(candidate),
                "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("结束时间早于开始时间", result.stderr)

    def test_validate_rejects_unknown_exam_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0].update(
                    {"exam_type": "public_institution"}
                ),
            )
            result = run_cli(
                "validate",
                "--input",
                str(candidate),
                "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("非法考试类型", result.stderr)

    def test_validate_requires_review_metadata_for_approved_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def remove_review_metadata(payload: dict) -> None:
                payload["exams"][0]["events"][0]["review"] = {"status": "approved"}

            candidate = self.write_mutated_fixture(directory, remove_review_metadata)
            result = run_cli(
                "validate",
                "--input",
                str(candidate),
                "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("已批准事件缺少审核时间", result.stderr)
        self.assertIn("已批准事件缺少审核人", result.stderr)

    def test_validate_binds_source_host_to_declared_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def use_mismatched_authority(payload: dict) -> None:
                for event in payload["exams"][0]["events"]:
                    event["source"]["authority_id"] = "national-civil-service"
                    event["source"]["url"] = "https://www.tj.gov.cn/official-notice"

            candidate = self.write_mutated_fixture(directory, use_mismatched_authority)
            registry = json.loads(
                (FIXTURES / "official-sources.json").read_text(encoding="utf-8")
            )
            registry["authorities"].append(
                {
                    "id": "tianjin-civil-service",
                    "name": "天津市公务员局",
                    "region_code": "120000",
                    "allowed_hosts": ["tj.gov.cn"],
                }
            )
            registry_path = Path(directory) / "sources.json"
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = run_cli(
                "validate",
                "--input",
                str(candidate),
                "--sources",
                str(registry_path),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("来源域名不属于声明的官方机构", result.stderr)

    def test_validate_binds_baseline_exam_region_to_authority_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(directory, lambda payload: None)
            registry = json.loads(
                (FIXTURES / "official-sources.json").read_text(encoding="utf-8")
            )
            registry["authorities"][0]["region_code"] = "120000"
            registry_path = Path(directory) / "sources.json"
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources", str(registry_path)
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("考试地区与官方机构地区不一致", result.stderr)

    def test_validate_rejects_selected_graduate_exam_outside_31_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0].update(
                    {"exam_type": "selected_graduate"}
                ),
            )
            result = run_cli(
                "validate",
                "--input",
                str(candidate),
                "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("选调生考试地区不适用: CN", result.stderr)

    def test_validate_requires_exact_notice_confirmation_for_approved_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def remove_exact_notice_confirmation(payload: dict) -> None:
                payload["exams"][0]["events"][0]["review"].pop(
                    "source_url_verified", None
                )

            candidate = self.write_mutated_fixture(
                directory, remove_exact_notice_confirmation
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("已批准事件必须确认精确公告链接", result.stderr)

    def test_validate_rejects_evidence_without_a_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0]["events"][0]["source"].update(
                    {"evidence": "详见公告"}
                ),
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("来源证据必须包含日期", result.stderr)

    def test_validate_rejects_entry_page_instead_of_exact_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0]["events"][0]["source"].update(
                    {"url": "https://bm.scs.gov.cn/kl2026"}
                ),
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("来源网址只是入口页", result.stderr)

    def test_validate_rejects_generic_index_page_as_exact_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0]["events"][0]["source"].update(
                    {"url": "https://www.beijing.gov.cn/fuwu/bmfw/sy/index.html"}
                ),
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("来源网址疑似栏目或入口页", result.stderr)

    def test_validate_accepts_index_page_inside_unique_article_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0]["events"][0]["source"].update(
                    {
                        "url": "https://www.beijing.gov.cn/fuwu/2026012809132190026/index.shtml"
                    }
                ),
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_validate_accepts_spa_document_route_with_unique_content_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0]["events"][0]["source"].update(
                    {
                        "url": "https://www.beijing.gov.cn/#/txt?contentId=unique-article-123"
                    }
                ),
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_validate_rejects_spa_document_route_without_content_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["exams"][0]["events"][0]["source"].update(
                    {"url": "https://www.beijing.gov.cn/#/txt?title=announcement"}
                ),
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("来源网址疑似栏目或入口页", result.stderr)

    def test_validate_rejects_verified_coverage_without_approved_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def mark_every_event_candidate(payload: dict) -> None:
                for event in payload["exams"][0]["events"]:
                    event["review"] = {"status": "candidate"}

            candidate = self.write_mutated_fixture(directory, mark_every_event_candidate)
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("coverage.verified 不能超过含已批准事件的辖区数", result.stderr)

    def test_validate_rejects_inconsistent_coverage_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.write_mutated_fixture(
                directory,
                lambda payload: payload["coverage"].update({"pending": 2}),
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("覆盖状态合计必须等于 expected", result.stderr)

    def test_validate_reports_malformed_exam_objects_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "malformed.json"
            candidate.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "coverage": {
                            "expected": 1, "verified": 0, "pending": 1,
                            "not_found": 0, "checked_at": "2026-08-27T20:00:00+08:00",
                        },
                        "exams": [None],
                    }
                ),
                encoding="utf-8",
            )
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("考试条目必须是对象", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_validate_rejects_inconsistent_scoped_coverage_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def add_bad_scope(payload: dict) -> None:
                payload["coverage_scopes"] = [
                    {
                        "recruitment_year": 2026,
                        "exam_type": "national",
                        "expected": 1,
                        "verified": 1,
                        "candidate": 0,
                        "pending": 1,
                        "not_found": 0,
                        "checked_at": "2026-08-27T20:10:00+08:00",
                        "regions": [
                            {
                                "region_code": "CN",
                                "region_name": "全国",
                                "status": "verified",
                            }
                        ],
                    }
                ]

            candidate = self.write_mutated_fixture(directory, add_bad_scope)
            result = run_cli(
                "validate", "--input", str(candidate), "--sources",
                str(FIXTURES / "official-sources.json"),
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("coverage_scopes[0] 覆盖状态合计必须等于 expected", result.stderr)


class InitCliTests(unittest.TestCase):
    def test_init_selected_graduate_creates_31_pending_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "2027" / "candidates.json"
            result = run_cli(
                "init",
                "--year",
                "2027",
                "--type",
                "selected_graduate",
                "--sources",
                str(ROOT / "references" / "official-sources.json"),
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            scope = payload["coverage_scopes"][0]
            self.assertEqual(scope["recruitment_year"], 2027)
            self.assertEqual(scope["exam_type"], "selected_graduate")
            self.assertEqual(scope["scope_mode"], "full")
            self.assertEqual(scope["expected"], 31)
            self.assertEqual(scope["pending"], 31)
            self.assertEqual(len(scope["regions"]), 31)
            self.assertNotIn("CN", {item["region_code"] for item in scope["regions"]})
            self.assertNotIn("660000", {item["region_code"] for item in scope["regions"]})
            validation = run_cli(
                "validate",
                "--input",
                str(output),
                "--sources",
                str(ROOT / "references" / "official-sources.json"),
            )

        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_init_region_creates_valid_partial_scope_not_fake_full_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "beijing.json"
            result = run_cli(
                "init",
                "--year",
                "2027",
                "--type",
                "selected_graduate",
                "--region",
                "110000",
                "--sources",
                str(ROOT / "references" / "official-sources.json"),
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            scope = payload["coverage_scopes"][0]
            self.assertEqual(scope["scope_mode"], "partial")
            self.assertEqual(scope["expected"], 1)
            validation = run_cli(
                "validate",
                "--input",
                str(output),
                "--sources",
                str(ROOT / "references" / "official-sources.json"),
            )
            self.assertEqual(validation.returncode, 0, validation.stderr)

            scope["scope_mode"] = "full"
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            invalid = run_cli(
                "validate",
                "--input",
                str(output),
                "--sources",
                str(ROOT / "references" / "official-sources.json"),
            )

        self.assertEqual(invalid.returncode, 1)
        self.assertIn("应包含 31 个地区", invalid.stderr)

    def test_init_refuses_to_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "candidates.json"
            output.write_text("keep me", encoding="utf-8")
            result = run_cli(
                "init",
                "--year",
                "2027",
                "--type",
                "selected_graduate",
                "--sources",
                str(ROOT / "references" / "official-sources.json"),
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("不能覆盖", result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "keep me")


class ReviewCliTests(unittest.TestCase):
    def test_review_writes_candidate_diff_and_coverage_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.json"
            payload = json.loads(
                (FIXTURES / "candidate-review-mixed.json").read_text(encoding="utf-8")
            )
            payload["coverage_scopes"] = [
                {
                    "recruitment_year": 2026,
                    "exam_type": "national",
                    "expected": 1,
                    "verified": 1,
                    "candidate": 0,
                    "pending": 0,
                    "not_found": 0,
                    "checked_at": "2026-08-27T20:10:00+08:00",
                    "regions": [
                        {
                            "region_code": "CN",
                            "region_name": "全国",
                            "status": "verified",
                        }
                    ],
                }
            ]
            candidate.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            report = Path(directory) / "review.md"
            result = run_cli(
                "review",
                "--candidate",
                str(candidate),
                "--published",
                str(FIXTURES / "published-one-exam.json"),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--output",
                str(report),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            review = report.read_text(encoding="utf-8")
            self.assertIn("候选更新审核", review)
            self.assertIn("cn-national-2026-registration", review)
            self.assertIn("确定性脚本不检查网页可达性", review)



class PromoteCliTests(unittest.TestCase):
    def test_promote_publishes_only_approved_events_and_writes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "published.json"
            history = Path(directory) / "history"
            result = run_cli(
                "promote",
                "--input",
                str(FIXTURES / "candidate-review-mixed.json"),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--output",
                str(output),
                "--history",
                str(history),
                "--review-id",
                "review-20260827",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            published = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                [event["id"] for event in published["exams"][0]["events"]],
                ["cn-national-2026-announcement"],
            )
            receipt = json.loads(
                (history / "review-20260827.json").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["added"], ["cn-national-2026-announcement"])
            self.assertEqual(receipt["skipped_unapproved"], ["cn-national-2026-registration"])
            self.assertEqual(
                published["coverage"],
                {
                    "expected": 1,
                    "verified": 1,
                    "pending": 0,
                    "not_found": 0,
                    "checked_at": "2026-08-27T20:10:00+08:00",
                },
            )

    def test_promote_rejects_review_id_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_cli(
                "promote",
                "--input",
                str(FIXTURES / "candidate-review-mixed.json"),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--output",
                str(Path(directory) / "published.json"),
                "--history",
                str(Path(directory) / "history"),
                "--review-id",
                "../escaped",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("审核批次 ID 格式非法", result.stderr)
            self.assertFalse((Path(directory) / "escaped.json").exists())

    def test_promote_rejects_malformed_existing_published_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "published.json"
            output.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "coverage": {
                            "expected": 1,
                            "verified": 1,
                            "pending": 0,
                            "not_found": 0,
                            "checked_at": "2026-08-27T20:00:00+08:00",
                        },
                        "exams": [None],
                    }
                ),
                encoding="utf-8",
            )
            result = run_cli(
                "promote",
                "--input",
                str(FIXTURES / "candidate-review-mixed.json"),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--output",
                str(output),
                "--history",
                str(Path(directory) / "history"),
                "--review-id",
                "review-existing-malformed",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("现有正式数据校验失败", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_promote_reserves_review_id_before_mutating_published_data(self) -> None:
        module_path = ROOT / "scripts" / "shangan_schedule.py"
        spec = importlib.util.spec_from_file_location("shangan_schedule_transaction", module_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "published.json"
            output.write_text(
                (FIXTURES / "published-one-exam.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            original_output = output.read_bytes()
            history = Path(directory) / "history"
            args = argparse.Namespace(
                input=str(FIXTURES / "candidate-review-mixed.json"),
                sources=str(FIXTURES / "official-sources.json"),
                output=str(output),
                history=str(history),
                review_id="review-interrupted",
            )
            real_write_json = module.write_json

            def interrupt_output_write(path: Path, payload: dict) -> None:
                if path == output:
                    raise OSError("simulated interruption")
                real_write_json(path, payload)

            with mock.patch.object(
                module, "write_json", side_effect=interrupt_output_write
            ):
                with self.assertRaisesRegex(OSError, "simulated interruption"):
                    module.command_promote(args)

            reservation = json.loads(
                (history / "review-interrupted.json").read_text(encoding="utf-8")
            )
            self.assertEqual(reservation["status"], "pending")
            self.assertEqual(output.read_bytes(), original_output)
            self.assertTrue((history / "review-interrupted-before.json").is_file())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(module.command_promote(args), 1)

    def test_promote_merges_coverage_scopes_without_dropping_other_years(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "published.json"
            published = json.loads(
                (FIXTURES / "published-one-exam.json").read_text(encoding="utf-8")
            )
            published["coverage_scopes"] = [
                {
                    "recruitment_year": 2025,
                    "exam_type": "national",
                    "expected": 1,
                    "verified": 0,
                    "candidate": 0,
                    "pending": 1,
                    "not_found": 0,
                    "checked_at": "2026-08-27T20:00:00+08:00",
                    "regions": [
                        {
                            "region_code": "CN",
                            "region_name": "全国",
                            "status": "pending",
                        }
                    ],
                }
            ]
            output.write_text(
                json.dumps(published, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            candidate = json.loads(
                (FIXTURES / "candidate-review-mixed.json").read_text(encoding="utf-8")
            )
            candidate["coverage_scopes"] = [
                {
                    "recruitment_year": 2026,
                    "exam_type": "national",
                    "expected": 1,
                    "verified": 1,
                    "candidate": 0,
                    "pending": 0,
                    "not_found": 0,
                    "checked_at": "2026-08-27T20:10:00+08:00",
                    "regions": [
                        {
                            "region_code": "CN",
                            "region_name": "全国",
                            "status": "verified",
                        }
                    ],
                }
            ]
            candidate_path = Path(directory) / "candidate.json"
            candidate_path.write_text(
                json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = run_cli(
                "promote",
                "--input",
                str(candidate_path),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--output",
                str(output),
                "--history",
                str(Path(directory) / "history"),
                "--review-id",
                "review-scope-merge",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                [(item["recruitment_year"], item["exam_type"]) for item in merged["coverage_scopes"]],
                [(2025, "national"), (2026, "national")],
            )

    def test_promote_merges_partial_region_without_shrinking_full_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "published.json"
            candidate = Path(directory) / "beijing.json"
            sources = ROOT / "references" / "official-sources.json"
            full = run_cli(
                "init", "--year", "2027", "--type", "selected_graduate",
                "--sources", str(sources), "--output", str(output),
            )
            partial = run_cli(
                "init", "--year", "2027", "--type", "selected_graduate",
                "--region", "110000", "--sources", str(sources),
                "--output", str(candidate),
            )
            self.assertEqual(full.returncode, 0, full.stderr)
            self.assertEqual(partial.returncode, 0, partial.stderr)

            result = run_cli(
                "promote",
                "--input",
                str(candidate),
                "--sources",
                str(sources),
                "--output",
                str(output),
                "--history",
                str(Path(directory) / "history"),
                "--review-id",
                "review-partial-merge",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            merged = json.loads(output.read_text(encoding="utf-8"))
            scope = merged["coverage_scopes"][0]
            self.assertEqual(scope["scope_mode"], "full")
            self.assertEqual(scope["expected"], 31)
            self.assertEqual(len(scope["regions"]), 31)

    def test_promote_rejects_partial_scope_without_full_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "beijing.json"
            output = Path(directory) / "published.json"
            sources = ROOT / "references" / "official-sources.json"
            initialized = run_cli(
                "init", "--year", "2027", "--type", "selected_graduate",
                "--region", "110000", "--sources", str(sources),
                "--output", str(candidate),
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)

            result = run_cli(
                "promote",
                "--input",
                str(candidate),
                "--sources",
                str(sources),
                "--output",
                str(output),
                "--history",
                str(Path(directory) / "history"),
                "--review-id",
                "review-partial-alone",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("局部覆盖范围必须合并", result.stderr)
            self.assertFalse(output.exists())


class BuildCliTests(unittest.TestCase):
    def test_build_creates_offline_static_site_with_source_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            result = run_cli(
                "build",
                "--data",
                str(FIXTURES / "published-one-exam.json"),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--assets",
                str(ROOT / "assets" / "site"),
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("上岸时间表", (output / "index.html").read_text(encoding="utf-8"))
            self.assertTrue((output / "styles.css").is_file())
            self.assertTrue((output / "app.js").is_file())
            data = json.loads((output / "data.json").read_text(encoding="utf-8"))
            self.assertEqual(
                data["exams"][0]["events"][0]["source"]["url"],
                "https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202510/t20251014_4222416.html",
            )

    def test_build_rejects_candidate_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            result = run_cli(
                "build",
                "--data",
                str(FIXTURES / "candidate-review-mixed.json"),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--assets",
                str(ROOT / "assets" / "site"),
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("只允许构建已批准事件", result.stderr)
            self.assertFalse((output / "index.html").exists())

    def test_build_rejects_partial_coverage_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "beijing.json"
            output = Path(directory) / "site"
            sources = ROOT / "references" / "official-sources.json"
            initialized = run_cli(
                "init", "--year", "2027", "--type", "selected_graduate",
                "--region", "110000", "--sources", str(sources),
                "--output", str(candidate),
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)

            result = run_cli(
                "build",
                "--data",
                str(candidate),
                "--sources",
                str(sources),
                "--assets",
                str(ROOT / "assets" / "site"),
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("局部覆盖范围不能直接构建网站", result.stderr)
            self.assertFalse((output / "index.html").exists())

    def test_build_rejects_approved_event_from_unofficial_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = json.loads(
                (FIXTURES / "candidate-unofficial-source.json").read_text(
                    encoding="utf-8"
                )
            )
            payload["exams"][0]["events"][0]["review"] = {
                "status": "approved",
                "reviewed_at": "2026-08-27T20:10:00+08:00",
                "reviewed_by": "maintainer",
            }
            candidate = Path(directory) / "unofficial-approved.json"
            candidate.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = run_cli(
                "build",
                "--data",
                str(candidate),
                "--sources",
                str(FIXTURES / "official-sources.json"),
                "--assets",
                str(ROOT / "assets" / "site"),
                "--output",
                str(Path(directory) / "site"),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("来源域名不在官方白名单", result.stderr)


class SnapshotRedirectTests(unittest.TestCase):
    def test_redirect_handler_rejects_unapproved_target_before_following(self) -> None:
        module_path = ROOT / "scripts" / "snapshot_official.py"
        spec = importlib.util.spec_from_file_location("snapshot_official", module_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        handler = module.WhitelistRedirectHandler({"official.example"})

        with self.assertRaisesRegex(ValueError, "重定向目标不在官方白名单"):
            handler.redirect_request(
                Request("https://official.example/notice"),
                None,
                302,
                "Found",
                {},
                "http://127.0.0.1/internal",
            )

    def test_detects_login_or_captcha_page_in_successful_html(self) -> None:
        module_path = ROOT / "scripts" / "snapshot_official.py"
        spec = importlib.util.spec_from_file_location("snapshot_official_block", module_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        blocked = module.detect_access_block(
            b'<html><title>Login</title><input type="password"><div class="captcha"></div></html>',
            "text/html; charset=utf-8",
            "https://official.example/login",
        )
        normal = module.detect_access_block(
            "<html><title>2026年度考试录用公务员公告</title><p>报名时间为2026年1月1日。</p></html>".encode(),
            "text/html; charset=utf-8",
            "https://official.example/notices/2026-01-01.html",
        )

        self.assertIsNotNone(blocked)
        self.assertIsNone(normal)


class SiteCalendarTests(unittest.TestCase):
    def test_calendar_renders_month_precision_events(self) -> None:
        script = r'''
const RealDate = Date;
global.Date = class extends RealDate {
  constructor(...args) {
    super(...(args.length ? args : ["2026-09-16T12:00:00+08:00"]));
  }
};
class FakeNode {
  constructor() {
    this.textContent = "";
    this.innerHTML = "";
    this.value = "all";
    this.hidden = false;
    this.selectedIndex = 0;
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.classList = { toggle() {} };
  }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  setAttribute() {}
  insertAdjacentHTML(position, html) {
    this.innerHTML = position === "afterbegin" ? html + this.innerHTML : this.innerHTML + html;
  }
}
const nodes = new Map();
const node = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, new FakeNode());
  return nodes.get(selector);
};
node("#timeline-tab").dataset.view = "timeline";
node("#calendar-tab").dataset.view = "calendar";
node("#schedule-data").textContent = JSON.stringify({
  coverage: { expected: 1, verified: 1, pending: 0, not_found: 0 },
  exams: [{
    id: "cn-national-2026", recruitment_year: 2026, title: "国考", exam_type: "national",
    region_code: "CN", region_name: "全国", events: [{
      id: "score-month", stage: "score", label: "成绩月份", start: "2026-01", end: "2026-01",
      precision: "month", status: "tentative",
      source: { url: "https://official.example/notices/score.html", title: "成绩公告", publisher: "官方", published_at: "2025-10-14" }
    }]
  }]
});
global.document = {
  querySelector: node,
  querySelectorAll(selector) {
    return selector === ".view-button" ? [node("#timeline-tab"), node("#calendar-tab")] : [];
  }
};
require(process.argv[1]);
node("#calendar-tab").listeners.click();
if (node("#calendar-view").hidden || node("#calendar-month").textContent !== "2026年9月") {
  console.error(`calendar did not open at the current month: ${node("#calendar-month").textContent}`);
  process.exit(1);
}
for (let index = 0; index < 8; index++) node("#previous-month").listeners.click();
if (node("#calendar-month").textContent !== "2026年1月") {
  console.error(`calendar navigation failed: ${node("#calendar-month").textContent}`);
  process.exit(1);
}
if (!node("#calendar-grid").innerHTML.includes("成绩月份")) {
  console.error("month precision event missing from calendar");
  process.exit(1);
}
node("#timeline-tab").listeners.click();
node("#calendar-tab").listeners.click();
if (node("#calendar-month").textContent !== "2026年1月") {
  console.error("calendar did not preserve the manually selected month");
  process.exit(1);
}
'''
        result = subprocess.run(
            ["node", "-e", script, str(ROOT / "assets" / "site" / "app.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_site_uses_scoped_2027_coverage_and_copies_update_prompt(self) -> None:
        script = r'''
class FakeNode {
  constructor() {
    this.textContent = "";
    this.innerHTML = "";
    this.value = "all";
    this.hidden = false;
    this.selectedIndex = 0;
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.classList = { toggle() {} };
  }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  setAttribute() {}
  insertAdjacentHTML(position, html) {
    this.innerHTML = position === "afterbegin" ? html + this.innerHTML : this.innerHTML + html;
  }
}
const nodes = new Map();
const node = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, new FakeNode());
  return nodes.get(selector);
};
node("#schedule-data").textContent = JSON.stringify({
  generated_at: "2026-09-12T10:00:00+08:00",
  coverage: { expected: 33, verified: 33, pending: 0, not_found: 0, checked_at: "2026-09-12T10:00:00+08:00" },
  coverage_scopes: [{
    recruitment_year: 2027, exam_type: "selected_graduate", scope_mode: "full", expected: 31,
    verified: 0, candidate: 0, pending: 31, not_found: 0,
    checked_at: "2026-09-12T11:00:00+08:00",
    regions: [
      { region_code: "110000", region_name: "北京", status: "pending" },
      { region_code: "310000", region_name: "上海", status: "pending" },
      ...Array.from({ length: 29 }, (_, index) => ({
        region_code: `T${String(index).padStart(5, "0")}`,
        region_name: `测试地区${index}`,
        status: "pending"
      }))
    ]
  }],
  exams: [{
    id: "cn-national-2026", recruitment_year: 2026, title: "国考", exam_type: "national",
    region_code: "CN", region_name: "全国", events: []
  }]
});
let copied = "";
Object.defineProperty(global, "navigator", {
  value: { clipboard: { writeText(value) { copied = value; return Promise.resolve(); } } },
  configurable: true
});
global.document = {
  querySelector: node,
  querySelectorAll() { return []; }
};
require(process.argv[1]);
if (node("#year-filter").value !== "2027") {
  console.error("newest scoped year was not selected");
  process.exit(1);
}
if (node("#type-filter").value !== "selected_graduate") {
  console.error("single scoped type was not selected");
  process.exit(1);
}
if (node("#coverage-count").textContent !== "0 / 31") {
  console.error(`wrong scoped coverage: ${node("#coverage-count").textContent}`);
  process.exit(1);
}
if (!node("#region-filter").innerHTML.includes('value="110000"')
    || node("#region-filter").innerHTML.includes('value="CN"')
    || node("#region-filter").innerHTML.includes('value="660000"')) {
  console.error(`wrong scoped region options: ${node("#region-filter").innerHTML}`);
  process.exit(1);
}
node("#copy-update-prompt").listeners.click();
setTimeout(() => {
  if (!copied.includes("2027 年全国选调生") || !copied.includes("先生成候选差异")) {
    console.error(`wrong copied prompt: ${copied}`);
    process.exit(1);
  }
}, 0);
'''
        result = subprocess.run(
            ["node", "-e", script, str(ROOT / "assets" / "site" / "app.js")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
