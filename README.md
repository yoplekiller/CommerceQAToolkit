# Commerce QA Toolkit

[![Tests](https://github.com/yoplekiller/CommerceQAToolkit/actions/workflows/tests.yml/badge.svg)](https://github.com/yoplekiller/CommerceQAToolkit/actions/workflows/tests.yml)

오픈마켓형 커머스 주문 서비스(테스트 대상 API)와, 테스트 사전조건을 명령 한 줄로 만들어주는 **QA 도구**
(주문 자동생성 / 상태 자동완료 / 장바구니 자동 담기 / 재고 조정 / 계정·쿠폰 발급 / 초기화).

> "배송완료된 쿠폰 주문으로 반품 테스트"를 하려면 원래는 장바구니 담기 → 결제 → 판매자 발송 → 배송완료까지 손으로 넘겨야 한다.
> 이 도구로는 `python qatool.py orders gen --scenario coupon_fixed --status DELIVERED` 한 줄이면 된다.

**🔗 라이브 데모: https://commerce-qa-toolkit.onrender.com** (무료 플랜이라 첫 접속은 30초 정도 걸릴 수 있음)

상세 명세(상태 흐름, 도메인 규칙 R1~R15, API 목록, 시드 데이터): [docs/SPEC.md](docs/SPEC.md)

## 화면 3개

실제 회사처럼 **고객 · 판매자 · QA** 가 각자 쓰는 화면을 나눴다. 셋 다 같은 서버/DB 를 보므로, 한 화면에서 바꾼 주문 상태가 다른 화면에 그대로 반영된다.

| 화면 | 주소 | 누가 | 하는 일 |
|---|---|---|---|
| **쇼핑몰** | [`/shop`](https://commerce-qa-toolkit.onrender.com/shop) | 고객 | 이메일 간이 로그인 → 상품 담기/바로구매 → 쿠폰 선택 → 결제 → 내 주문내역(진행 단계, 취소 · 반품 신청 · 구매확정) |
| **판매자 어드민** | [`/admin`](https://commerce-qa-toolkit.onrender.com/admin) | 판매자 · 택배사 | 상태 탭(건수) → 주문 확인 · 발송 · 배송완료 · 반품 승인 · 판매자 취소, 일괄 처리, **상품 등록 · 가격/재고/구매 제한 수정 · 판매중지** |
| **QA 도구** | [`/`](https://commerce-qa-toolkit.onrender.com/) | 테스터 | 위 두 화면을 테스트할 데이터를 미리 만듦: 주문 자동생성 · 상태 자동완료 · 재고/품절 · 계정 · 쿠폰 · 장바구니 |

예) 어드민 "반품 승인" 화면을 테스트하려면 원래 쇼핑몰에서 주문 → 어드민에서 발송 → 배송완료 → 쇼핑몰에서 반품 신청까지 해야 한다. QA 도구에서 `반품요청` 상태 주문을 바로 만들면 그 준비가 버튼 한 번이다.

| 쇼핑몰 | 판매자 어드민 |
|---|---|
| ![쇼핑몰](docs/shop.png) | ![판매자 어드민](docs/admin.png) |

![QA 도구](docs/screenshot.png)

- 쇼핑몰의 배송비 · 할인 · 결제금액은 화면에서 계산하지 않고 서버 **주문서 계산 API(`/orders/quote`)** 결과를 그대로 보여준다 → 화면 금액 = 실제 결제 금액
- 어드민 처리 버튼과 QA 도구 상태 이동 메뉴는 서버 상태 전이표 범위만 노출, 일괄 처리는 건별 결과(성공/실패)를 모아 알려준다
- 모든 조작 요소에 `data-testid`
- 데모라 로그인은 이메일만(비밀번호 없음), 판매자 어드민은 인증 없이 열려 있다

### 공개 데모 배포 (Render)

데모: https://commerce-qa-toolkit.onrender.com · API 문서: https://commerce-qa-toolkit.onrender.com/docs

`render.yaml`(Render Blueprint) 설정으로 배포되고, main 에 push 하면 자동 재배포된다. 데모는 `DEMO_MODE=true` 라서 화면이 QA 토큰을 자동으로 채운다(가짜 데이터라 공개해도 되는 경우에만 켤 것).
무료 플랜은 15분간 요청이 없으면 잠들고 깨어날 때 데이터가 초기화된다(`AUTO_SEED=true` 로 기본 상품 자동 생성). 첫 접속은 30초 정도 걸릴 수 있다.

## Slack 슬래시 커맨드 (`/qa`)

QA 도구를 Slack 에서 바로 쓴다. 데이터를 바꾼 결과는 채널에 공개돼 팀이 "누가 어떤 테스트 데이터를 만들었는지" 같이 본다.

```
/qa 주문 3건 정률쿠폰 배송완료      주문 자동생성 + 목표 상태까지 진행 (옵션 순서 자유, 회원=ID 지정 가능)
/qa 상태 12 반품완료                주문 상태 자동완료 (정상 단계를 한 단계씩, 이력에 slack 으로 기록)
/qa 조회 12                         주문 상세 + 처리 이력
/qa 재고 4 0                        재고 설정 (0 = 품절)
/qa 상품 테스트 머그컵 19799 5        상품 등록 (판매자 등록과 같은 검사, 이름에 공백 가능)
/qa 계정 3                          테스트 계정 생성
/qa 쿠폰 5 정액 3000 -1             쿠폰 발급 (만료일수 음수 = 만료 쿠폰)
/qa 초기화 확인                      기본 데이터로 초기화 ('확인' 없으면 실행 안 됨)
/qa 내권한                           내 Slack ID · 역할 확인
/qa 로그 20                          최근 실행 기록 (누가 · 언제 · 무엇 · 성공/거부/실패)
/qa help                            도움말
```

```
✅ 주문 3건 생성 (정률쿠폰 → 배송완료) · 회원 #4
• #1 배송완료 · 화장지 30롤×1 · 무료배송 · 할인 1,980원 · 결제 17,820원
• #2 배송완료 · 생수 2L x 6×1 · 배송비 3,000원 · 할인 598원 · 결제 8,382원
• #3 배송완료 · 무선 이어폰×1 · 무료배송 · 할인 5,000원 · 결제 84,000원
판매자 어드민 · 쇼핑몰 · QA 도구
```

- Slack 요청 **서명(HMAC-SHA256) 검증**, 5분 넘은 요청은 재전송 공격으로 보고 거부. `SLACK_SIGNING_SECRET` 이 없으면 엔드포인트 자체가 404
- 서비스 규칙 위반(예: 배송중 → 취소)은 에러 코드와 함께 **본인에게만** 보임
- **권한(역할 · 채널)**: 등록된 사람만 쓴다. 서명 검증은 "Slack 에서 왔는지", 권한은 "누가 보냈는지"(Slack 이 보내는 user_id · channel_id)로 판단

  | 역할 | 할 수 있는 것 | 예 |
  |---|---|---|
  | none (미등록) | help, 내권한 | 누구나 |
  | viewer | + 조회 | 개발자 · 기획자 |
  | tester | + 주문 · 상태 · 재고 · 계정 · 쿠폰 · 상품 | QA 엔지니어 |
  | admin | + 초기화 · 로그 | QA 리드 |

  권한 설정은 [`slack/permissions.example.yaml`](slack/permissions.example.yaml) 형식을 Render 환경변수 `SLACK_PERMISSIONS` 에 넣는다 (권한 변경에 커밋 불필요). **설정이 없으면 전부 잠김.** 허용 채널 지정 가능
- **감사 로그**: 모든 명령(성공 · 거부 · 실패)을 기록, 데이터 초기화에도 지워지지 않음. 주문 이력엔 `slack:이름` 으로 실행자가 남는다
- **사용량 제한**: 사람별 1분 10회(데이터 변경 명령만, `SLACK_RATE_LIMIT_PER_MINUTE`)
- 명령어 해석 · 서명 검증 · 실행을 나눠서 Slack 없이 테스트 (`tests/test_slack_bot.py` 58건)

### 연결 방법 (한 번만)
1. https://api.slack.com/apps → **Create New App → From a manifest** → 워크스페이스 선택 → [`slack/manifest.yml`](slack/manifest.yml) 내용 붙여넣기 → Create
2. **Install to Workspace**
3. **Basic Information → Signing Secret** 복사 → Render 서비스 **Environment** 에 `SLACK_SIGNING_SECRET` 으로 추가 (저장하면 재배포)
4. Slack 에서 `/qa 내권한` → 나온 사용자 ID 를 `SLACK_PERMISSIONS` 에 `admin` 으로 등록 (팀원도 같은 방식)
5. `/qa help`

> 무료 Render 는 잠들어 있으면 깨어나는 데 30초쯤 걸리는데 Slack 은 3초 안에 응답을 받아야 한다. 잠든 상태에서 첫 명령은 `dispatch_failed` 가 날 수 있으니, 데모 주소를 한 번 열어 깨운 뒤 쓰면 된다.

## 실제 서비스에 연결하기 (설정 파일만 채우기)

QA 도구(주문 자동생성 · 상태 자동완료 · 재고)를 **이 레포의 데모 쇼핑몰이 아닌 다른 서비스**에 붙일 수 있다. 코드는 그대로 두고 **설정 파일의 빈칸만** 채운다.

```bash
cp targets/template.yaml targets/my-shop.yaml     # TODO 칸 채우기 (API 주소 · 인증 · 엔드포인트 · 상태값 · 응답 필드)
export MY_SHOP_TOKEN=...                           # 비밀값은 설정 파일에 ${MY_SHOP_TOKEN} 으로

python qatool.py target check  targets/my-shop.yaml      # 빈칸 · 연결 · 필드 매핑 점검 (읽기 요청만)
python qatool.py target orders targets/my-shop.yaml --count 3 --scenario free_shipping --status DELIVERED
python qatool.py target advance targets/my-shop.yaml O-0012 --to CONFIRMED
```

```
$ python qatool.py target check targets/qa-mall.yaml
  ✅ 설정 형식 OK · 대상 QA Mall (데모) (staging) http://127.0.0.1:8000
  ✅ 연결 OK · 상품 8개 조회, 필드 매핑(id/price/stock) 확인
  ✅ 주문 가능한 상품 6개
  ✅ 쿠폰 주문 (coupon_fixed / coupon_percent): 사용 가능
  ...
$ python qatool.py target check targets/template.yaml
  ❌ 아직 안 채운 칸: base_url
  ❌ 아직 안 채운 칸: statuses.PAID
  ...
```

| 채우는 칸 | 예 |
|---|---|
| `base_url`, `environment` | 스테이징 API 주소. `production` 이면 실행 거부 |
| `auth.headers` | `Authorization: Bearer ${TOKEN}` |
| `statuses` | 표준 상태 → 그 서비스 상태값 (`PAID: ORDER_COMPLETE`) |
| `transitions` | 주문 흐름이 다르면 수정 (반품이 없는 서비스 등) |
| `fields` | 응답 필드 이름 매핑, 중첩 가능 (`price: salePrice.amount`) |
| `endpoints` | 필수: 상품 조회 · 주문 생성 · 주문 조회 · 상태 변경(상태별 URL 가능) / 선택: 회원 생성 · 쿠폰 발급 · 재고 조정 |

- 예시 2개: [`targets/qa-mall.yaml`](targets/qa-mall.yaml)(이 레포 데모를 **공개 API 만으로** 연결 — 이력에 customer/seller/carrier 로 남음), `tests/test_target_adapter.py` 의 **경로 · 응답 구조 · 상태값 · ID 형식이 전혀 다른 가짜 쇼핑몰**
- 선택 엔드포인트가 없으면 그 기능만 꺼지고, 무엇이 없어서 안 되는지 알려준다 (예: 쿠폰 API 없음 → 쿠폰 시나리오 불가)
- 구조: `qa_target/config.py`(설정 · 빈칸 · 운영 차단) → `adapter.py`(설정대로 HTTP 호출 · 응답 변환) → `engine.py`(서비스와 무관한 주문 생성 · 상태 진행 로직, 내장 QA 도구도 같은 상품 선택 함수 사용)

## 실행

```bash
pip install -r requirements.txt
uvicorn app.main:create_app --factory --reload   # 웹 화면 http://127.0.0.1:8000 · API 문서 /docs
```

## QA 도구 CLI

```bash
python qatool.py seed                                   # 초기화 + 기본 상품 8종
python qatool.py users --count 5                        # 테스트 계정 5개
python qatool.py coupon --user 1 --type FIXED --value 3000 --expires-days -1   # 만료 쿠폰
python qatool.py stock 4 0                              # 4번 상품 품절 만들기
python qatool.py cart --user 1 --count 3                # 장바구니 자동 담기
python qatool.py orders gen --count 3 --scenario coupon_percent --status DELIVERED --seed 1
python qatool.py orders advance 1 --to RETURNED         # 주문 상태 자동완료
python qatool.py orders show 1                          # 상세 + 상태 이력
```

```
  ID              상태  상품합계  배송비    할인  결제금액  상품
   1         DELIVERED     5,980   3,000     598     8,382  생수 2L x 6(1개)
   2         DELIVERED    19,800       0   1,980    17,820  화장지 30롤(1개)
   3         DELIVERED    89,000       0   5,000    84,000  무선 이어폰(1개)

$ python qatool.py orders advance 1 --to RETURNED
이력: PAID -> PREPARING -> SHIPPING -> DELIVERED -> RETURN_REQUESTED -> RETURNED
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `DB_PATH` | `commerce.db` | SQLite 파일 |
| `QA_TOKEN` | `local-qa-token` | QA 도구 API `X-QA-Token` 헤더 값 |
| `QA_TOOLS_ENABLED` | `true` | `false`면 `/qa/*` 전체 404 (운영 노출 방지) |
| `QA_BASE_URL` | `http://127.0.0.1:8000` | CLI가 호출할 서버 |
| `DEMO_MODE` | `false` | `true`면 웹 화면에 QA 토큰 자동 입력 (공개 데모용) |
| `AUTO_SEED` | `false` | `true`면 시작 시 상품이 없을 때 시드 데이터 생성 |
| `SLACK_SIGNING_SECRET` | (없음) | Slack 앱 Signing Secret. 없으면 `/slack/commands` 404 |
| `SLACK_PERMISSIONS` | (없음 = 전부 잠김) | Slack 사용자별 역할 · 허용 채널 (YAML) |
| `SLACK_RATE_LIMIT_PER_MINUTE` | `10` | 사람별 1분당 데이터 변경 명령 수 |
| `PUBLIC_BASE_URL` | 요청 주소 | Slack 메시지 하단 링크에 쓸 공개 주소 |

## 설계 포인트

- **자동완료는 DB 덮어쓰기가 아니라 허용된 전이를 한 단계씩 밟는다** (목표 상태까지 최단 경로를 BFS로 계산) → 상태 이력이 실제 흐름과 동일하게 남고, 취소/반품 시 재고·쿠폰 복구도 실제와 똑같이 일어난다.
- **주문 자동생성/장바구니 담기도 서비스와 같은 로직을 거친다** → 품절·판매중지·구매제한 초과 같은 "실제로는 생길 수 없는 데이터"가 만들어지지 않는다.
- 재고 차감은 `UPDATE ... WHERE stock >= ?` 조건부 갱신으로 초과 판매를 막는다.
- `--seed`로 같은 주문 데이터를 재현할 수 있다.
- QA 도구 API는 토큰 + 기능 플래그로 보호.

## 테스트

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium

pytest                 # 전체 249건 (끝에 SPEC 규칙 R1~R15 별 테스트 수 출력)
pytest -m "not e2e"    # 브라우저 없이 API + QA 도구만
pytest -m e2e          # 웹 화면 E2E 만
pytest --rule R7       # 특정 규칙(docs/SPEC.md) 테스트만
```

| 위치 | 내용 | 수 |
|---|---|---|
| `tests/api/` | 서비스 API: 도메인 규칙 R1~R16(상품 등록/수정 경계값 포함), 경계값(무료배송 19,799/19,800, 수량 0/1/99/100, 구매제한, 쿠폰 최소주문·최대할인), 상태 전이 전체, 주문서 계산·로그인·주문내역·어드민 목록, 상품 수정이 기존 주문에 영향 없음 | 132 |
| `tests/e2e/` | 웹 화면 (Playwright, `data-testid` 로케이터). QA 도구: 주문 생성, 상태 이동·이력, 이동 가능 상태만 노출, 품절, 에러 토스트, 초기화 2단계 / 쇼핑몰·어드민: 비로그인 차단, 품절 버튼 비활성, 무료배송 경계 안내, 구매제한 에러, 쿠폰 적용, 결제 후 장바구니 비움, 배송 전만 취소, 상태 탭 건수·필터, 일괄 처리 부분 실패, **고객↔판매자 탭을 오가는 주문→발송→배송완료→반품→승인→재고 복구 전체 흐름**, 어드민 상품 등록→쇼핑몰 노출, 잘못된 상품 에러, 재고 0·판매중지가 쇼핑몰 버튼에 반영 | 20 |
| `tests/test_qa_tools.py` | QA 도구 자체 검증 | 23 |
| `tests/test_target_adapter.py` | 외부 서비스 연결: 템플릿 빈칸 감지, 운영 환경 거부, 환경변수 치환, 본문 템플릿, 데모 쇼핑몰을 설정만으로 연결(주문 → 배송완료 · 반품 → 재고 복구 · 서비스 에러 전달 · 잘못된 필드 매핑 감지), **모양이 다른 가짜 쇼핑몰**(중첩 필드 · 판매중지 제외 · 반품/쿠폰 미지원 안내 · 토큰 오류) | 16 |
| `tests/test_slack_bot.py` | Slack `/qa`: 명령어 해석(한국어 별칭 · 건수 경계값 · 잘못된 입력), 서명 검증(변조 · 다른 시크릿 · 5분 경계), 엔드포인트 실행(주문 생성 · 상품 등록 · 규칙 위반은 비공개 · 초기화 확인), **권한**(역할 매트릭스 11케이스 · 채널 제한 · 설정 없으면 잠김 · 거부 안내), 사용량 제한(60초 경계), 감사 로그(성공/거부/실패 · 초기화 후 유지), 이력에 실행자 이름 | 58 |

- 사전조건(계정·쿠폰·재고·특정 상태 주문)은 전부 **QA 도구(`/qa/*`)로 준비**하고, 테스트 본문엔 검증 대상 호출과 assert 만 둔다.
- CI 는 API / E2E 잡을 나눠 돌리고, JUnit XML 과 E2E 실패 시 Playwright trace·스크린샷을 artifact 로 올린다.
- 변경 기록: [docs/changes/](docs/changes/)

## 로드맵

- [x] v1: 서비스 API + QA 도구 API + CLI
- [x] 서비스 API 자동화 테스트 스위트 (pytest, 도메인 규칙 R1~R15) + 웹 화면 E2E
- [x] v2: QA 도구 웹 화면 + Render 배포 설정
- [x] v3: 쇼핑몰(`/shop`) + 판매자 어드민(`/admin`) 화면, 주문서 계산 API
- [x] v4: Slack 슬래시 커맨드 `/qa` (서명 검증, 매니페스트로 앱 생성)
- [x] v4.1: Slack 권한(역할 · 채널) · 감사 로그 · 사용량 제한
- [x] v5: 외부 서비스 연결 — 설정 파일(`targets/*.yaml`) 빈칸만 채우면 다른 서비스에 QA 도구 적용
