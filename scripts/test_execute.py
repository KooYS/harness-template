"""
execute.py 리팩터링 안전망 테스트.
리팩터링 전후 동작이 동일한지 검증한다.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import execute as ex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """phases/, CLAUDE.md, docs/ 를 갖춘 임시 프로젝트 구조."""
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()

    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Rules\n- rule one\n- rule two")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "arch.md").write_text("# Architecture\nSome content")
    (docs_dir / "guide.md").write_text("# Guide\nAnother doc")

    return tmp_path


@pytest.fixture
def phase_dir(tmp_project):
    """step 3개를 가진 phase 디렉토리."""
    d = tmp_project / "phases" / "0-mvp"
    d.mkdir()

    index = {
        "project": "TestProject",
        "phase": "mvp",
        "steps": [
            {"step": 0, "name": "setup", "status": "completed", "summary": "프로젝트 초기화 완료"},
            {"step": 1, "name": "core", "status": "completed", "summary": "핵심 로직 구현"},
            {"step": 2, "name": "ui", "status": "pending"},
        ],
    }
    (d / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    (d / "step2.md").write_text("# Step 2: UI\n\nUI를 구현하세요.")

    return d


@pytest.fixture
def top_index(tmp_project):
    """phases/index.json (top-level)."""
    top = {
        "phases": [
            {"dir": "0-mvp", "status": "pending"},
            {"dir": "1-polish", "status": "pending"},
        ]
    }
    p = tmp_project / "phases" / "index.json"
    p.write_text(json.dumps(top, indent=2))
    return p


@pytest.fixture
def executor(tmp_project, phase_dir):
    """테스트용 StepExecutor 인스턴스. git 호출은 별도 mock 필요."""
    return ex.StepExecutor("0-mvp", root=tmp_project)


# ---------------------------------------------------------------------------
# _stamp (= 이전 now_iso)
# ---------------------------------------------------------------------------

class TestStamp:
    def test_returns_kst_timestamp(self, executor):
        result = executor._stamp()
        assert "+0900" in result

    def test_format_is_iso(self, executor):
        result = executor._stamp()
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert dt.tzinfo is not None

    def test_is_current_time(self, executor):
        before = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0)
        result = executor._stamp()
        after = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0) + timedelta(seconds=1)
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert before <= parsed <= after


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------

class TestJsonHelpers:
    def test_roundtrip(self, tmp_path):
        data = {"key": "값", "nested": [1, 2, 3]}
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, data)
        loaded = ex.StepExecutor._read_json(p)
        assert loaded == data

    def test_save_ensures_ascii_false(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"한글": "테스트"})
        raw = p.read_text()
        assert "한글" in raw
        assert "\\u" not in raw

    def test_save_indented(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"a": 1})
        raw = p.read_text()
        assert "\n" in raw

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ex.StepExecutor._read_json(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# _load_guardrails
# ---------------------------------------------------------------------------

class TestLoadGuardrails:
    def test_loads_claude_md_and_docs(self, executor):
        result = executor._load_guardrails()
        assert "# Rules" in result
        assert "rule one" in result
        assert "# Architecture" in result
        assert "# Guide" in result

    def test_sections_separated_by_divider(self, executor):
        result = executor._load_guardrails()
        assert "---" in result

    def test_docs_sorted_alphabetically(self, executor):
        result = executor._load_guardrails()
        arch_pos = result.index("arch")
        guide_pos = result.index("guide")
        assert arch_pos < guide_pos

    def test_no_claude_md(self, executor, tmp_project):
        (tmp_project / "CLAUDE.md").unlink()
        result = executor._load_guardrails()
        assert "CLAUDE.md" not in result
        assert "Architecture" in result

    def test_no_docs_dir(self, executor, tmp_project):
        import shutil
        shutil.rmtree(tmp_project / "docs")
        result = executor._load_guardrails()
        assert "Rules" in result
        assert "Architecture" not in result

    def test_empty_project(self, tmp_path):
        # _root가 빈 디렉토리를 가리키는 인스턴스
        inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_path)
        result = inst._load_guardrails()
        assert result == ""


# ---------------------------------------------------------------------------
# _build_step_context
# ---------------------------------------------------------------------------

class TestBuildStepContext:
    def test_includes_completed_with_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "Step 0 (setup): 프로젝트 초기화 완료" in result
        assert "Step 1 (core): 핵심 로직 구현" in result

    def test_excludes_pending(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "ui" not in result

    def test_excludes_completed_without_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        del index["steps"][0]["summary"]
        result = ex.StepExecutor._build_step_context(index)
        assert "setup" not in result
        assert "core" in result

    def test_empty_when_no_completed(self):
        index = {"steps": [{"step": 0, "name": "a", "status": "pending"}]}
        result = ex.StepExecutor._build_step_context(index)
        assert result == ""

    def test_has_header(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert result.startswith("## 이전 Step 산출물")


# ---------------------------------------------------------------------------
# _build_preamble
# ---------------------------------------------------------------------------

class TestBuildPreamble:
    def test_includes_project_name(self, executor):
        result = executor._build_preamble("", "")
        assert "TestProject" in result

    def test_includes_guardrails(self, executor):
        result = executor._build_preamble("GUARD_CONTENT", "")
        assert "GUARD_CONTENT" in result

    def test_includes_step_context(self, executor):
        ctx = "## 이전 Step 산출물\n\n- Step 0: done"
        result = executor._build_preamble("", ctx)
        assert "이전 Step 산출물" in result

    def test_instructs_no_commit(self, executor):
        result = executor._build_preamble("", "")
        assert "커밋하지 마라" in result
        assert "executor가 자동으로" in result

    def test_includes_rules(self, executor):
        result = executor._build_preamble("", "")
        assert "작업 규칙" in result
        assert "AC" in result

    def test_no_retry_section_by_default(self, executor):
        result = executor._build_preamble("", "")
        assert "이전 시도 실패" not in result

    def test_retry_section_with_prev_error(self, executor):
        result = executor._build_preamble("", "", prev_error="타입 에러 발생")
        assert "이전 시도 실패" in result
        assert "타입 에러 발생" in result

    def test_includes_max_retries(self, executor):
        result = executor._build_preamble("", "")
        assert str(ex.StepExecutor.MAX_RETRIES) in result

    def test_includes_index_path(self, executor):
        result = executor._build_preamble("", "")
        assert "/phases/0-mvp/index.json" in result


# ---------------------------------------------------------------------------
# _update_top_index
# ---------------------------------------------------------------------------

class TestUpdateTopIndex:
    def test_completed(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "completed"
        assert "completed_at" in mvp

    def test_error(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("error")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "error"
        assert "failed_at" in mvp

    def test_blocked(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("blocked")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "blocked"
        assert "blocked_at" in mvp

    def test_other_phases_unchanged(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        polish = next(p for p in data["phases"] if p["dir"] == "1-polish")
        assert polish["status"] == "pending"

    def test_nonexistent_dir_is_noop(self, executor, top_index):
        executor._top_index_file = top_index
        executor._phase_dir_name = "no-such-dir"
        original = json.loads(top_index.read_text())
        executor._update_top_index("completed")
        after = json.loads(top_index.read_text())
        for p_before, p_after in zip(original["phases"], after["phases"]):
            assert p_before["status"] == p_after["status"]

    def test_no_top_index_file(self, executor, tmp_path):
        executor._top_index_file = tmp_path / "nonexistent.json"
        executor._update_top_index("completed")  # should not raise


# ---------------------------------------------------------------------------
# _checkout_branch (mocked)
# ---------------------------------------------------------------------------

class TestCheckoutBranch:
    def _mock_git(self, executor, responses):
        call_idx = {"i": 0}
        def fake_git(*args):
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx < len(responses):
                return responses[idx]
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

    def test_already_on_branch(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="feat-mvp\n", stderr=""),
        ])
        executor._checkout_branch()  # should return without checkout

    def test_branch_exists_checkout(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_branch_not_exists_create(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="not found"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_checkout_fails_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="dirty tree"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1

    def test_no_git_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=1, stdout="", stderr="not a git repo"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _commit_step (mocked)
# ---------------------------------------------------------------------------

class TestCommitStep:
    def test_two_phase_commit(self, executor):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 2
        assert "feat(mvp):" in commit_calls[0][2]
        assert "chore(mvp):" in commit_calls[1][2]

    def test_unstages_secret_patterns(self, executor):
        """시크릿 패턴(.env, *.pem 등)을 feat 커밋에서 unstage."""
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=0)  # no changes to commit
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        reset_calls = [c for c in calls if c[0] == "reset" and c[1] == "HEAD"]
        reset_targets = [c[-1] for c in reset_calls]
        assert ".env" in reset_targets
        assert "*.pem" in reset_targets
        assert "*.key" in reset_targets
        assert "credentials*" in reset_targets

    def test_chore_commit_only_adds_index(self, executor):
        """chore 커밋은 index.json만 stage한다 (git add -A 아님)."""
        call_count = {"diff": 0}
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                call_count["diff"] += 1
                if call_count["diff"] == 1:
                    return MagicMock(returncode=0)  # no feat changes
                return MagicMock(returncode=1)  # has chore changes
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        # chore 단계에서 add 대상이 index.json만인지 확인
        # feat 단계의 "add -A" 이후, chore 단계에서는 index_rel만 add
        add_calls = [c for c in calls if c[0] == "add"]
        chore_add = add_calls[-1]  # 마지막 add가 chore용
        assert chore_add == ("add", f"phases/0-mvp/index.json")

    def test_no_code_changes_skips_feat_commit(self, executor):
        call_count = {"diff": 0}
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                call_count["diff"] += 1
                if call_count["diff"] == 1:
                    return MagicMock(returncode=0)
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        commit_msgs = [c[2] for c in calls if c[0] == "commit"]
        assert len(commit_msgs) == 1
        assert "chore" in commit_msgs[0]


# ---------------------------------------------------------------------------
# _invoke_claude (mocked)
# ---------------------------------------------------------------------------

class TestInvokeClaude:
    def test_invokes_claude_with_correct_args(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"result": "ok"}', stderr="")
        step = {"step": 2, "name": "ui"}
        preamble = "PREAMBLE\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            output = executor._invoke_claude(step, preamble)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--output-format" in cmd
        # prompt은 CLI 인자가 아닌 stdin으로 전달 (ARG_MAX 회피)
        stdin_input = mock_run.call_args[1]["input"]
        assert "PREAMBLE" in stdin_input
        assert "UI를 구현하세요" in stdin_input

    def test_saves_output_json(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result):
            executor._invoke_claude(step, "preamble")

        output_file = executor._phase_dir / "step2-output.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["step"] == 2
        assert data["name"] == "ui"
        assert data["exitCode"] == 0

    def test_nonexistent_step_file_exits(self, executor):
        step = {"step": 99, "name": "nonexistent"}
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_claude(step, "preamble")
        assert exc_info.value.code == 1

    def test_timeout_is_1800(self, executor):
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_claude(step, "preamble")

        assert mock_run.call_args[1]["timeout"] == 1800


# ---------------------------------------------------------------------------
# progress_indicator (= 이전 Spinner)
# ---------------------------------------------------------------------------

class TestProgressIndicator:
    def test_context_manager(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.15)
        assert pi.elapsed >= 0.1

    def test_elapsed_increases(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.2)
        assert pi.elapsed > 0


# ---------------------------------------------------------------------------
# main() CLI 파싱 (mocked)
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_no_args_exits(self):
        with patch("sys.argv", ["execute.py"]):
            with pytest.raises(SystemExit) as exc_info:
                ex.main()
            assert exc_info.value.code == 2  # argparse exits with 2

    def test_invalid_phase_dir_exits(self, tmp_path):
        with patch("sys.argv", ["execute.py", "nonexistent"]):
            with patch.object(ex, "ROOT", tmp_path):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1

    def test_missing_index_exits(self, tmp_project):
        (tmp_project / "phases" / "empty").mkdir()
        with patch("sys.argv", ["execute.py", "empty"]):
            with patch.object(ex, "ROOT", tmp_project):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_blockers (= 이전 main() error/blocked 체크)
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def _make_executor_with_steps(self, tmp_project, steps):
        d = tmp_project / "phases" / "test-phase"
        d.mkdir(exist_ok=True)
        index = {"project": "T", "phase": "test", "steps": steps}
        (d / "index.json").write_text(json.dumps(index))
        return ex.StepExecutor("test-phase", root=tmp_project)

    def test_error_step_exits_1(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "bad", "status": "error", "error_message": "fail"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    def test_blocked_step_exits_2(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "stuck", "status": "blocked", "blocked_reason": "API key"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 2

    def test_all_pending_passes(self, tmp_project):
        steps = [
            {"step": 0, "name": "a", "status": "pending"},
            {"step": 1, "name": "b", "status": "pending"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        inst._check_blockers()  # should not raise

    def test_all_completed_passes(self, tmp_project):
        steps = [
            {"step": 0, "name": "a", "status": "completed"},
            {"step": 1, "name": "b", "status": "completed"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        inst._check_blockers()  # should not raise

    def test_error_behind_completed_is_ignored(self, tmp_project):
        """Step 0 error + Step 1 completed → 역순 순회 시 completed에서 break."""
        steps = [
            {"step": 0, "name": "old-err", "status": "error", "error_message": "old"},
            {"step": 1, "name": "ok", "status": "completed"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        inst._check_blockers()  # should not raise — error is behind completed


# ---------------------------------------------------------------------------
# _print_header
# ---------------------------------------------------------------------------

class TestPrintHeader:
    def test_prints_phase_info(self, executor, capsys):
        executor._print_header()
        captured = capsys.readouterr()
        assert "mvp" in captured.out
        assert str(executor._total) in captured.out

    def test_shows_auto_push(self, executor, capsys):
        executor._auto_push = True
        executor._print_header()
        captured = capsys.readouterr()
        assert "Auto-push" in captured.out

    def test_hides_auto_push_when_false(self, executor, capsys):
        executor._auto_push = False
        executor._print_header()
        captured = capsys.readouterr()
        assert "Auto-push" not in captured.out


# ---------------------------------------------------------------------------
# _ensure_created_at
# ---------------------------------------------------------------------------

class TestEnsureCreatedAt:
    def test_adds_created_at_when_missing(self, executor):
        executor._ensure_created_at()
        index = ex.StepExecutor._read_json(executor._index_file)
        assert "created_at" in index
        assert "+0900" in index["created_at"]

    def test_does_not_overwrite_existing(self, executor):
        index = ex.StepExecutor._read_json(executor._index_file)
        index["created_at"] = "2025-01-01T00:00:00+0900"
        ex.StepExecutor._write_json(executor._index_file, index)

        executor._ensure_created_at()
        index = ex.StepExecutor._read_json(executor._index_file)
        assert index["created_at"] == "2025-01-01T00:00:00+0900"


# ---------------------------------------------------------------------------
# _invoke_claude — 비정상 종료 & timeout
# ---------------------------------------------------------------------------

class TestInvokeClaudeEdgeCases:
    def test_nonzero_exit_code_captures_stderr(self, executor, capsys):
        mock_result = MagicMock(returncode=1, stdout='{}', stderr="Claude crashed")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result):
            output = executor._invoke_claude(step, "preamble")

        assert output["exitCode"] == 1
        captured = capsys.readouterr()
        assert "WARN" in captured.out
        assert "Claude crashed" in captured.out

    def test_timeout_returns_gracefully(self, executor, capsys):
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 1800)):
            output = executor._invoke_claude(step, "preamble")

        assert output["exitCode"] == -1
        assert "timeout" in output["stderr"]
        captured = capsys.readouterr()
        assert "타임아웃" in captured.out


# ---------------------------------------------------------------------------
# _commit_step — 커밋 실패 경고 경로
# ---------------------------------------------------------------------------

class TestCommitStepEdgeCases:
    def test_feat_commit_fails_warns(self, executor, capsys):
        call_count = {"diff": 0}
        def fake_git(*args):
            if args[:2] == ("diff", "--cached"):
                call_count["diff"] += 1
                return MagicMock(returncode=1)  # has staged changes
            if args[0] == "commit":
                if "feat" in args[2]:
                    return MagicMock(returncode=1, stderr="hook failed")
                return MagicMock(returncode=0, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")
        captured = capsys.readouterr()
        assert "WARN" in captured.out
        assert "코드 커밋 실패" in captured.out

    def test_chore_commit_fails_warns(self, executor, capsys):
        call_count = {"diff": 0}
        def fake_git(*args):
            if args[:2] == ("diff", "--cached"):
                call_count["diff"] += 1
                if call_count["diff"] == 1:
                    return MagicMock(returncode=0)  # no feat changes
                return MagicMock(returncode=1)  # has chore changes
            if args[0] == "commit":
                return MagicMock(returncode=1, stderr="hook failed")
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")
        captured = capsys.readouterr()
        assert "WARN" in captured.out
        assert "housekeeping 커밋 실패" in captured.out

    def test_no_changes_at_all_no_commit(self, executor):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=0)  # no changes
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")
        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 0


# ---------------------------------------------------------------------------
# _execute_single_step (mocked)
# ---------------------------------------------------------------------------

class TestExecuteSingleStep:
    def _setup_executor(self, executor, claude_sets_status):
        """Claude 호출 시 index.json의 step status를 변경하는 mock 설정."""
        def fake_invoke(step, preamble):
            index = ex.StepExecutor._read_json(executor._index_file)
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = claude_sets_status
                    if claude_sets_status == "completed":
                        s["summary"] = "done"
                    elif claude_sets_status == "error":
                        s["error_message"] = "something broke"
                    elif claude_sets_status == "blocked":
                        s["blocked_reason"] = "need API key"
            ex.StepExecutor._write_json(executor._index_file, index)
            return {"step": step["step"], "name": step["name"], "exitCode": 0, "stdout": "", "stderr": ""}

        executor._invoke_claude = fake_invoke
        executor._commit_step = MagicMock()

    def test_completed_returns_true(self, executor, capsys):
        self._setup_executor(executor, "completed")
        step = {"step": 2, "name": "ui"}
        result = executor._execute_single_step(step, "guardrails")
        assert result is True
        captured = capsys.readouterr()
        assert "✓" in captured.out

    def test_blocked_exits_2(self, executor):
        self._setup_executor(executor, "blocked")
        executor._update_top_index = MagicMock()
        step = {"step": 2, "name": "ui"}
        with pytest.raises(SystemExit) as exc_info:
            executor._execute_single_step(step, "guardrails")
        assert exc_info.value.code == 2

    def test_error_retries_then_exits(self, executor, capsys):
        """Claude가 계속 에러를 내면 MAX_RETRIES 후 exit(1)."""
        self._setup_executor(executor, "error")
        executor._update_top_index = MagicMock()
        step = {"step": 2, "name": "ui"}
        with pytest.raises(SystemExit) as exc_info:
            executor._execute_single_step(step, "guardrails")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "retry" in captured.out

    def test_pending_unchanged_retries_and_fails(self, executor, capsys):
        """Claude가 status를 업데이트하지 않으면 MAX_RETRIES 후 실패."""
        # Don't change status — leave as pending
        executor._invoke_claude = MagicMock(return_value={
            "step": 2, "name": "ui", "exitCode": 0, "stdout": "", "stderr": ""
        })
        executor._commit_step = MagicMock()
        executor._update_top_index = MagicMock()
        step = {"step": 2, "name": "ui"}
        with pytest.raises(SystemExit) as exc_info:
            executor._execute_single_step(step, "guardrails")
        assert exc_info.value.code == 1

    def test_completed_records_timestamp(self, executor):
        self._setup_executor(executor, "completed")
        step = {"step": 2, "name": "ui"}
        executor._execute_single_step(step, "guardrails")
        index = ex.StepExecutor._read_json(executor._index_file)
        step_data = next(s for s in index["steps"] if s["step"] == 2)
        assert "completed_at" in step_data
        assert "+0900" in step_data["completed_at"]

    def test_blocked_records_timestamp(self, executor):
        self._setup_executor(executor, "blocked")
        executor._update_top_index = MagicMock()
        step = {"step": 2, "name": "ui"}
        with pytest.raises(SystemExit):
            executor._execute_single_step(step, "guardrails")
        index = ex.StepExecutor._read_json(executor._index_file)
        step_data = next(s for s in index["steps"] if s["step"] == 2)
        assert "blocked_at" in step_data


# ---------------------------------------------------------------------------
# _execute_all_steps (mocked)
# ---------------------------------------------------------------------------

class TestExecuteAllSteps:
    def test_runs_all_pending_steps(self, executor):
        """pending step을 모두 실행한다."""
        executed = []

        def fake_single_step(step, guardrails):
            executed.append(step["step"])
            index = ex.StepExecutor._read_json(executor._index_file)
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "completed"
                    s["summary"] = f"step {step['step']} done"
            ex.StepExecutor._write_json(executor._index_file, index)
            return True

        executor._execute_single_step = fake_single_step
        executor._execute_all_steps("guardrails")
        assert executed == [2]  # only step 2 is pending

    def test_skips_completed_steps(self, executor):
        """이미 completed인 step은 건너뛴다."""
        executed = []

        def fake_single_step(step, guardrails):
            executed.append(step["step"])
            index = ex.StepExecutor._read_json(executor._index_file)
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "completed"
                    s["summary"] = "done"
            ex.StepExecutor._write_json(executor._index_file, index)
            return True

        executor._execute_single_step = fake_single_step
        executor._execute_all_steps("guardrails")
        # step 0, 1 already completed; only step 2 should run
        assert 0 not in executed
        assert 1 not in executed

    def test_records_started_at(self, executor):
        """pending step 시작 시 started_at 기록."""
        def fake_single_step(step, guardrails):
            index = ex.StepExecutor._read_json(executor._index_file)
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "completed"
                    s["summary"] = "done"
            ex.StepExecutor._write_json(executor._index_file, index)
            return True

        executor._execute_single_step = fake_single_step
        executor._execute_all_steps("guardrails")

        index = ex.StepExecutor._read_json(executor._index_file)
        step2 = next(s for s in index["steps"] if s["step"] == 2)
        assert "started_at" in step2

    def test_no_pending_returns_immediately(self, executor):
        """모든 step이 completed이면 즉시 반환."""
        index = ex.StepExecutor._read_json(executor._index_file)
        for s in index["steps"]:
            s["status"] = "completed"
            s["summary"] = "done"
        ex.StepExecutor._write_json(executor._index_file, index)

        executor._execute_single_step = MagicMock()
        executor._execute_all_steps("guardrails")
        executor._execute_single_step.assert_not_called()


# ---------------------------------------------------------------------------
# _finalize (mocked)
# ---------------------------------------------------------------------------

class TestFinalize:
    def _make_executor_for_finalize(self, executor, top_index):
        executor._top_index_file = top_index
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        return executor

    def test_sets_completed_at(self, executor, top_index):
        self._make_executor_for_finalize(executor, top_index)
        executor._finalize()
        index = ex.StepExecutor._read_json(executor._index_file)
        assert "completed_at" in index

    def test_updates_top_index(self, executor, top_index):
        self._make_executor_for_finalize(executor, top_index)
        executor._finalize()
        top = ex.StepExecutor._read_json(top_index)
        mvp = next(p for p in top["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "completed"

    def test_commits_when_changes_exist(self, executor, top_index):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:3] == ("diff", "--cached", "--quiet"):
                return MagicMock(returncode=1)  # has changes
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._top_index_file = top_index

        executor._finalize()
        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 1
        assert "mark phase completed" in commit_calls[0][2]

    def test_no_commit_when_clean(self, executor, top_index):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:3] == ("diff", "--cached", "--quiet"):
                return MagicMock(returncode=0)  # no changes
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._top_index_file = top_index

        executor._finalize()
        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 0

    def test_push_when_auto_push(self, executor, top_index, capsys):
        calls = []
        def fake_git(*args):
            calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._top_index_file = top_index
        executor._auto_push = True

        executor._finalize()
        push_calls = [c for c in calls if c[0] == "push"]
        assert len(push_calls) == 1
        assert "feat-mvp" in push_calls[0]
        captured = capsys.readouterr()
        assert "Pushed" in captured.out

    def test_push_failure_exits(self, executor, top_index):
        def fake_git(*args):
            if args[0] == "push":
                return MagicMock(returncode=1, stderr="remote rejected")
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._top_index_file = top_index
        executor._auto_push = True

        with pytest.raises(SystemExit) as exc_info:
            executor._finalize()
        assert exc_info.value.code == 1

    def test_no_push_when_disabled(self, executor, top_index):
        calls = []
        def fake_git(*args):
            calls.append(args)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        executor._top_index_file = top_index
        executor._auto_push = False

        executor._finalize()
        push_calls = [c for c in calls if c[0] == "push"]
        assert len(push_calls) == 0


# ---------------------------------------------------------------------------
# run() — 통합 (mocked)
# ---------------------------------------------------------------------------

class TestRun:
    def test_orchestration_order(self, executor, top_index):
        """run()이 올바른 순서로 메서드를 호출하는지 확인."""
        call_order = []
        executor._top_index_file = top_index

        executor._print_header = lambda: call_order.append("header")
        executor._check_blockers = lambda: call_order.append("blockers")
        executor._checkout_branch = lambda: call_order.append("branch")
        executor._load_guardrails = lambda: (call_order.append("guardrails"), "")[1]
        executor._ensure_created_at = lambda: call_order.append("created_at")
        executor._execute_all_steps = lambda g: call_order.append("execute")
        executor._finalize = lambda: call_order.append("finalize")

        executor.run()
        assert call_order == [
            "header", "blockers", "branch", "guardrails", "created_at", "execute", "finalize"
        ]
