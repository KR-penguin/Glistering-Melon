# 실행 방법

프론트엔드는 이 PC에서, 백엔드는 집 서버(`192.168.45.100`)에서 돕니다.
백엔드는 `restart: unless-stopped` 라서 **서버가 켜져 있으면 자동으로 떠 있습니다** —
평소에는 프론트만 켜면 됩니다.

---

## 1. 프론트엔드 (이 PC)

`start-frontend.bat` 더블클릭. 또는 터미널에서:

```bash
python -m http.server 5599
```

리포지토리 폴더에서 실행해야 합니다. 그다음 브라우저로:

**http://localhost:5599/Landing.dc.html**

멈추려면 그 창에서 `Ctrl+C`.

> 백엔드가 LAN 주소(`192.168.45.100`)라서 **같은 네트워크에 있어야** 동작합니다.
> 외부에서 접속하려면 포트포워딩 + 도메인이 필요합니다.

---

## 2. 백엔드 (집 서버)

### 상태 확인

```bash
ssh sean@192.168.45.100 "docker ps --filter name=gm- && curl -s localhost:8000/healthz"
```

`gm-backend`가 `Up`이고 `{"ok":true}`가 나오면 정상입니다.

### 꺼져 있을 때 시작

```bash
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon/backend && docker compose up -d"
```

### 재시작 (설정 바꾼 뒤)

```bash
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon/backend && docker compose up -d --build"
```

### 중지

```bash
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon/backend && docker compose down"
```

손님 마인크래프트 서버는 별도 컨테이너라 `down` 해도 계속 돕니다.

### 로그 보기

```bash
ssh sean@192.168.45.100 "docker logs -f --tail 50 gm-backend"
```

---

## 주소 모음

| | 주소 |
|---|---|
| 랜딩 페이지 | http://localhost:5599/Landing.dc.html |
| 로그인 | http://localhost:8000/login (SSH 터널 필요 — 위 "구글 설정" 참고) |
| 관리자 | http://192.168.45.100:8000/admin |
| 서버 상태 | http://192.168.45.100:8000/healthz |

관리자 토큰은 서버의 `.env`에 있습니다:

```bash
ssh sean@192.168.45.100 "grep ^ADMIN_TOKEN= /home/sean/glistering-melon/backend/.env"
```

---

## 처음 한 번: 구글 설정

지금은 **AI 매니저와 로그인만** 이 설정을 기다리고 있습니다. 서버 개통, 입금
타이머, 관리자 페이지는 설정 없이도 그대로 돌아갑니다.

### 1. Vertex AI (AI 매니저)

Google Cloud 콘솔에서:

1. 프로젝트를 만들고 **Vertex AI API**를 켭니다.
2. 서비스 계정을 만들고 **Vertex AI User** 역할을 줍니다.
3. 그 계정의 JSON 키를 내려받아 서버의
   `/srv/minecraft/data/gcp-credentials.json` 에 둡니다:

```bash
scp ~/Downloads/키파일.json sean@192.168.45.100:/srv/minecraft/data/gcp-credentials.json
```

> 리포지토리 밖(`HOST_DATA_ROOT` 안)에 두는 이유가 있습니다. 그 경로는 이미
> 컨테이너에 마운트돼 있어서 따로 마운트할 필요가 없고, 파일 하나를 직접
> 마운트하면 파일이 없을 때 도커가 그 자리에 *디렉터리*를 만들어 버립니다.

`.env` 에는 비밀이 아닌 값만 들어갑니다:

```
GOOGLE_CLOUD_PROJECT=내-프로젝트-아이디
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-3.7-flash
```

`GEMINI_MODEL` 은 프로젝트에서 쓸 수 있는 모델로 맞춰야 합니다. 확인:

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -c \"
from app.config import settings
from google import genai
c = genai.Client(vertexai=True, project=settings.google_cloud_project, location=settings.google_cloud_location)
print([m.name for m in c.models.list()][:40])\""
```

### 2. 구글 로그인

콘솔의 **APIs & Services → Credentials** 에서 **OAuth 2.0 Client ID**를
`Web application` 타입으로 만들고, 승인된 리디렉션 URI에 아래를 **한 글자도
다르지 않게** 넣습니다:

```
http://localhost:8000/api/auth/google/callback
```

구글은 사설 IP(`192.168.45.100`)를 리디렉션 주소로 받아주지 않습니다. 그래서
도메인이 붙기 전까지는 localhost로 두고, 터널로 접속해서 테스트합니다:

```bash
ssh -L 8000:localhost:8000 sean@192.168.45.100
```

그다음 브라우저에서 **http://localhost:8000/login** 으로 들어갑니다.

`.env`:

```
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
OAUTH_REDIRECT_BASE=http://localhost:8000
```

도메인이 생기면 `OAUTH_REDIRECT_BASE` 를 비우면 됩니다 — 그러면
`PUBLIC_BASE_URL` 을 따라갑니다.

---

## 코드 바꾼 뒤 배포

```bash
git archive --format=tar.gz -o /tmp/b.tar.gz HEAD backend
scp /tmp/b.tar.gz sean@192.168.45.100:/home/sean/glistering-melon/
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon && tar xzf b.tar.gz && rm b.tar.gz && cd backend && docker compose up -d --build"
```

`gcp-credentials.json` 은 리포지토리 밖(`/srv/minecraft/data/`)에 있어서
배포해도 덮어써지지 않습니다.

`--build`가 반드시 필요합니다. 코드는 이미지 빌드 시점에 복사되므로 `--force-recreate`
만으로는 **옛 코드가 그대로 돕니다** — 파일은 서버에 올라갔는데 동작은 안 바뀌는,
알아채기 어려운 상태가 됩니다.

리포지토리가 private이라 서버가 직접 `git pull`을 못 합니다. GitHub에 deploy key를
등록하면 서버에서 바로 받아올 수 있습니다.

---

## 저장 위치

| | 경로 | 디스크 |
|---|---|---|
| 손님 월드 | `/mnt/hdd/minecraft/worlds/` | HDD 2.7TB |
| DB | `/srv/minecraft/data/glistering_melon.db` | SSD |

월드는 용량이 크니 HDD, DB는 작고 잦은 읽기/쓰기가 있으니 SSD로 나눠 뒀습니다.
`.env`의 `HOST_WORLDS_ROOT`로 바꿀 수 있습니다.

---

## DB 보기

SQLite 파일 하나입니다: 서버의 `/srv/minecraft/data/glistering_melon.db`

**전체 요약** — 테이블별 행 수와 주요 컬럼:

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview"
```

**특정 테이블만:**

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview servers"
```

테이블: `servers`, `users`, `applications`, `auth_tokens`

**모든 컬럼 보기** (기본은 주요 컬럼만):

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview servers --all-columns"
```

**직접 조회:**

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview --sql \"SELECT nickname, status, payment_status FROM servers\""
```

읽기 전용이라 `SELECT`만 됩니다. 실수로 고객 데이터를 바꿀 수 없게 SQLite를
읽기 전용 모드로 열고, `SELECT`가 아닌 문은 거부합니다. 값을 바꾸실 일은
관리자 페이지에서 하세요.

비밀번호 해시·RCON 비밀번호·세션 토큰은 출력에서 가려집니다.

**파일 백업** (통째로 복사):

```bash
scp sean@192.168.45.100:/srv/minecraft/data/glistering_melon.db ./backup.db
```

---

## 자주 쓰는 것

**손님 서버 목록**

```bash
ssh sean@192.168.45.100 "docker ps --filter name=gm- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

**테스트 데이터 정리** (서버·계정 전부 삭제, 되돌릴 수 없음)

```bash
ssh sean@192.168.45.100 'for C in $(docker ps -aq --filter name=gm- --format "{{.Names}}" | grep -v backend); do docker rm -f $C; sudo rm -rf /srv/minecraft/data/worlds/$C; done; docker exec -i gm-backend python -c "
from app.db import get_connection
c=get_connection()
[c.execute(f\"DELETE FROM {t}\") for t in (\"auth_tokens\",\"users\",\"servers\",\"applications\")]
c.commit(); c.close()"'
```

**명령어 레퍼런스 갱신** (마인크래프트 새 버전 나왔을 때)

```bash
cd backend && python -m agent.rag.ingest --version 26.3
```

diff를 확인하고 커밋하세요.
