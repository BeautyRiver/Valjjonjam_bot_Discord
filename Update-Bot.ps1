[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$project = 'project-df59d642-8234-4a5d-859'
$zone = 'us-west1-a'
$target = 'kmg05027@valjjonjam-bot'

try {
    $gcloud = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if (-not $gcloud) {
        throw 'Google Cloud CLI를 찾지 못했습니다. 설치 후 창을 다시 열어주세요.'
    }

    Write-Host '발쫀봇 서버 업데이트' -ForegroundColor Cyan
    Write-Host "대상: $target / $zone"
    Write-Host "프로젝트: $project"
    Write-Host 'GitHub main의 최신 코드를 적용하고 봇만 재시작합니다.'
    Write-Host '로컬 변경사항을 커밋하거나 푸시하지는 않습니다.'
    Write-Host ''
    if ((Read-Host '진행하려면 Y를 입력하세요 (그 외 입력은 취소)') -ne 'Y') {
        Write-Host '취소했습니다. 서버는 변경하지 않았습니다.'
        return
    }

    $activeAccount = & $gcloud auth list '--filter=status:ACTIVE' '--format=value(account)'
    if ($LASTEXITCODE -ne 0) {
        throw 'Google Cloud 로그인 상태를 확인하지 못했습니다. 위 오류를 확인해주세요.'
    }
    if (-not $activeAccount) {
        throw 'PowerShell에서 gcloud auth login 으로 로그인한 다음 다시 실행해주세요.'
    }
    Write-Host "사용 계정: $activeAccount"

    # Keep this a single line without nested quotes for Windows native argument passing.
    # Stop on any error. Never overwrite server edits or create a merge commit.
    $remoteCommand = @(
        'set -eu'
        'cd ~/bot'
        'test -f bot.py'
        'test $(git branch --show-current) = main'
        'git diff --quiet'
        'git diff --cached --quiet'
        'sudo -n true'
        'git pull --ff-only origin main'
        'sudo -n systemctl restart valjjonjam'
        'sleep 3'
        'sudo -n systemctl is-active valjjonjam'
        'git log -1 --oneline'
    ) -join '; '

    & $gcloud compute ssh $target "--project=$project" "--zone=$zone" "--command=$remoteCommand"
    if ($LASTEXITCODE -ne 0) {
        throw '업데이트 또는 실행 확인에 실패했습니다. 일부 단계는 적용됐을 수 있으니 위 오류를 확인해주세요.'
    }

    Write-Host ''
    Write-Host '업데이트 완료! 봇 서비스가 실행 중입니다.' -ForegroundColor Green
    Write-Host 'Discord에서도 봇 온라인 상태와 명령어 동작을 확인해주세요.'
}
catch {
    Write-Host "실패: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
