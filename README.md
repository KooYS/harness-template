# Harness

> Claude Code한테 일을 시키는 프레임워크
> step 단위로 쪼개서 자동 실행하고, 가드레일 주입 · 자가 교정(최대 3회) · 자동 커밋까지

---

## 전체 흐름

```mermaid
flowchart LR
    A["뼈대 클론"] --> B["기획\n(AI와 함께)"]
    B --> C["하네스 설계\n(같이)"]
    C --> D["실행\n(AI 혼자)"]
    D --> E["리뷰\n(같이)"]
```

| Step | Who | What |
| :--- | :---: | :--- |
| 뼈대 | **나** | 레포 클론해서 시작점 만들기 |
| 기획 | **나** + AI | ⭐ docs/ 같이 채우기 (PRD, ARCHITECTURE, ADR) |
| 하네스 설계 | **나** + AI | ⭐ CLAUDE.md 규칙 잡고 Hooks 세팅 |
| 실행 | AI | /harness → execute.py로 Phase 쭉 돌리기 |
| 리뷰 | **나** + AI | ⭐ 결과 확인하고, 부족하면 docs 보강 후 재실행 |

---

## 시작하기

### 1. 클론 + 셋업

```bash
git clone https://github.com/KooYS/harness-template.git project
cd project
npm install
```

<br>

### 2. docs/ 채우기 — AI랑 같이

Claude Code 열고 대화하면서 docs/를 만들어 나간다.

```
나: "단축 URL 서비스를 만들자."
AI: PRD 초안 제안 → 핵심 기능, MVP 제외사항 정리
나: "단축 URL 접속 시 카운트 증가하고 원본 URL로 리다이렉트 시키자."
AI: PRD 수정 → ARCHITECTURE.md, ADR.md도 함께 작성
```

> 혼자 끙끙대며 쓸 필요 없다. **AI랑 같이 기획**하면 놓치기 쉬운 부분도 잡아준다.

<br>

### 3. CLAUDE.md에 CRITICAL 규칙 박기

AI가 절대 어기면 안 되는 규칙을 여기에 넣는다. 예를 들면:

- `CRITICAL: DB 스키마 변경 시 마이그레이션 파일 필수`
- `CRITICAL: 외부 라이브러리 추가 전 docs/ADR.md에 사유 기록`
- `CRITICAL: 컴포넌트 하나당 파일 하나, 200줄 초과 금지`

<br>

### 4. 환경변수 세팅

프로젝트에 필요한 키가 있으면 `.env`에 넣어둔다.

```bash
echo "NEXT_PUBLIC_YOUTUBE_API_KEY=your_key
NEXT_PUBLIC_CLAUDE_API_KEY=your_key" > .env
```

<br>

### 5. /harness 실행

Claude Code에서 `/harness` 치면 된다. AI가 docs/를 읽고 Phase별 구현 계획을 짜준다.

<br>

### 6. execute.py로 자동 실행 (선택)

> `/harness`가 execute.py 실행까지 포함하고 있어서, Step 5에서 이미 돌렸으면 이 단계는 넘겨도 된다.
> 특정 Phase만 다시 돌리고 싶을 때 쓰면 된다.

```bash
python3 scripts/execute.py task-name
```

<br>

### 7. 리뷰 + 다듬기

결과물 확인하고, 아쉬운 부분이 있으면 docs/ 보강한 다음 다시 돌린다.

---

## 결과물이 마음에 안 들 때

docs/를 보강하면 된다. `.md` 하나 추가하는 것만으로 다음 실행부터 AI의 행동 범위가 좁아진다.

| 이런 문제가 있으면 | 손볼 문서 |
| :--- | :--- |
| **폴더 구조를 맘대로 바꿈** | `ARCHITECTURE.md` 제약 조건 명시 — *"pages/ 아래에만 라우트 파일 생성, lib/은 순수 유틸만"* |
| **인증 처리가 뒤죽박죽** | `CLAUDE.md` CRITICAL 규칙 추가 — *"CRITICAL: 인증 로직은 middleware.ts 한 곳에서만 처리"* |
| **상태 관리가 제각각** | `ADR.md` 이유 보강 — *"왜 Redux 대신 Zustand를 골랐는지, 어디까지 전역으로 둘지"* |
| **반응형이 깨짐** | `docs/UI_GUIDE.md` 추가 — *브레이크포인트 기준, 모바일 우선 레이아웃 규칙* |
| **에러 응답이 들쭉날쭉** | `docs/API_SPEC.md` 추가 — *에러 코드 체계, 공통 응답 포맷 `{ code, message, data }` 정의* |

> 결과 확인 → 보강을 반복하면서 결과물의 점수가 높아진다.

---

## execute.py는 뭘 하나

실행하면 사람이 신경 쓸 부분을 대신 처리해준다.

| # | 기능 | 설명 |
| :---: | :--- | :--- |
| 1 | **브랜치 분리** | `feat-{task-name}`으로 작업 공간을 나눠서 main을 안 건드림 |
| 2 | **가드레일 자동 주입** | CLAUDE.md와 docs/ 안의 `.md`들을 step마다 프롬프트에 붙여줌 |
| 3 | **이전 결과 이어받기** | 앞 step에서 뭘 했는지 요약본을 다음 step에 넘김 |
| 4 | **실패 시 재도전** | 에러가 나면 그 에러 내용을 힌트 삼아 최대 3회 다시 돌림 |
| 5 | **커밋 자동 분리** | 코드는 `feat:`, 설정/메타는 `chore:`로 나눠서 커밋 |

<br>

### 뭔가 멈췄을 때

`phases/{task-name}/index.json`을 열어서 직접 고치면 된다.

| 상태 | 조치 |
| :--- | :--- |
| **error** | `status`를 `"pending"`으로, `error_message`를 지우고 다시 돌리기 |
| **blocked** | `blocked_reason`에 적힌 걸 해결한 뒤 `status`를 `"pending"`으로 바꾸고 재실행 |

---

## 디렉토리 구조

```
docs/               프로젝트 설계 문서 (PRD, ARCHITECTURE, ADR, UI_GUIDE)
scripts/
  execute.py        step 실행 엔진
  test_execute.py   execute.py 테스트
phases/             실행 시 생성되는 phase/step 데이터
.claude/
  commands/         Claude Code 커스텀 커맨드 (/harness, /review)
  settings.json     hooks 설정 (위험 명령어 차단, 테스트 자동 실행)
```
