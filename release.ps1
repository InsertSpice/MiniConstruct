[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$')]
    [string]$Version,
    [string]$DevRepo,
    [string]$PublicRepo,
    [string]$GitHubRepo,
    [string]$DevBranch = 'master',
    [string]$PythonExe,
    [string]$ReleaseNotesPath,
    [switch]$RefreshDevelopmentSource,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# This script is intentionally a cautious release *orchestrator*.  It never
# resets, cleans, force-pushes, or merges public-release history into GitHub.
$VersionFiles = @(
    @{ Path = 'pyproject.toml'; Pattern = 'version\s*=\s*"[^"]+"'; Replacement = 'version = "{0}"' },
    @{ Path = 'miniconstruct/__init__.py'; Pattern = '__version__\s*=\s*"[^"]+"'; Replacement = '__version__ = "{0}"' },
    @{ Path = 'miniconstruct/app.py'; Pattern = 'version\s*=\s*"[^"]+"'; Replacement = 'version="{0}"' },
    @{ Path = 'miniconstruct/api/routes.py'; Pattern = '"version"\s*:\s*"[^"]+"'; Replacement = '"version": "{0}"' }
)

function Write-ReleaseInfo([string]$Message) { Write-Host "[release] $Message" }
function Stop-Release([string]$Message) { throw "RELEASE STOPPED: $Message" }

function ConvertTo-ProcessArgument([string]$Value) {
    # CommandLineToArgvW-compatible quoting for Windows PowerShell 5.1 as
    # well as PowerShell 7.  It preserves spaces, quotes, and trailing slashes.
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes++
        }
        elseif ($character -eq [char]34) {
            [void]$builder.Append([char]92, ($backslashes * 2 + 1))
            [void]$builder.Append([char]34)
            $backslashes = 0
        }
        else {
            if ($backslashes) { [void]$builder.Append([char]92, $backslashes) }
            [void]$builder.Append($character)
            $backslashes = 0
        }
    }
    if ($backslashes) { [void]$builder.Append([char]92, ($backslashes * 2)) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory,
        [switch]$AllowFailure
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    if ($WorkingDirectory) { $startInfo.WorkingDirectory = $WorkingDirectory }
    $startInfo.Arguments = (($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { Stop-Release "Could not start $Label ($FilePath)." }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $result = [pscustomobject]@{
        ExitCode = $process.ExitCode
        Output = ($stdoutTask.GetAwaiter().GetResult() + $stderrTask.GetAwaiter().GetResult())
    }
    if ($result.ExitCode -ne 0 -and -not $AllowFailure) {
        $detail = if ($result.Output) { "`n$($result.Output)" } else { '' }
        Stop-Release "$Label failed with exit code $($result.ExitCode).$detail"
    }
    return $result
}

function Invoke-Git {
    param([string]$Repo, [string]$Label, [string[]]$Arguments, [switch]$AllowFailure)
    return Invoke-External -Label "$Label [$Repo]" -FilePath 'git' -Arguments (@('-C', $Repo) + $Arguments) -AllowFailure:$AllowFailure
}

function Get-GitText {
    param([string]$Repo, [string]$Label, [string[]]$Arguments)
    return (Invoke-Git -Repo $Repo -Label $Label -Arguments $Arguments).Output.Trim()
}

function Test-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { Stop-Release "Required executable '$Name' was not found on PATH." }
}

function Resolve-RepositoryPath([string]$Path, [string]$Label) {
    if (-not $Path) { Stop-Release "$Label path was not supplied." }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { Stop-Release "$Label does not exist: $Path" }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Assert-GitRepository([string]$Repo, [string]$Label, [string]$ExpectedBranch) {
    $inside = Get-GitText $Repo $Label @('rev-parse', '--is-inside-work-tree')
    if ($inside -ne 'true') { Stop-Release "$Label is not a Git working tree: $Repo" }
    $branch = Get-GitText $Repo $Label @('branch', '--show-current')
    if ($branch -ne $ExpectedBranch) { Stop-Release "$Label must be on '$ExpectedBranch', but is on '$branch': $Repo" }
}

function Get-MergeInProgress([string]$Repo, [string]$Label) {
    return (Invoke-Git -Repo $Repo -Label $Label -Arguments @('rev-parse', '-q', '--verify', 'MERGE_HEAD') -AllowFailure).ExitCode -eq 0
}

function Assert-RepositoryClean {
    param([string]$Repo, [string]$Label, [switch]$PermitResolvedMerge)
    $status = Get-GitText $Repo $Label @('status', '--porcelain')
    if (-not $status) { return }
    if ($PermitResolvedMerge -and (Get-MergeInProgress $Repo $Label)) {
        $unmerged = Get-GitText $Repo $Label @('diff', '--name-only', '--diff-filter=U')
        if ($unmerged) { Stop-Release "$Label has an in-progress merge with unresolved paths:`n$unmerged`nResolve and stage them; do not abort the merge." }
        $unstaged = (Invoke-Git -Repo $Repo -Label $Label -Arguments @('diff', '--quiet') -AllowFailure).ExitCode
        if ($unstaged -ne 0) { Stop-Release "$Label has an in-progress merge with unstaged changes. Resolve and stage every change before rerunning." }
        Write-ReleaseInfo "$Label has a resolved, staged merge and may be resumed."
        return
    }
    Stop-Release "$Label has uncommitted changes and will not be modified:`n$status"
}

function Get-ManagedOfficialGuideTargets([string]$Repo, [string]$Python) {
    $result = Invoke-External -Label 'Read managed official-guide targets' -FilePath $Python -Arguments @('-B', '-m', 'miniconstruct.h3.guide_acquisition', '--managed-targets') -WorkingDirectory $Repo
    $paths = @($result.Output -split "`r?`n" | Where-Object { $_ })
    if (-not $paths.Count) { Stop-Release 'Official-guide acquisition did not report any managed target paths.' }
    $invalid = @($paths | Where-Object { $_ -notmatch '^miniconstruct/h3/guides/[^/\\]+$' -or $_ -match '\.\.' })
    if ($invalid.Count -or @($paths | Select-Object -Unique).Count -ne $paths.Count) {
        Stop-Release "Official-guide acquisition reported invalid or duplicate target paths: $($paths -join ', ')"
    }
    return $paths
}

function Assert-ManagedOfficialGuideTargets([string]$Repo, [string]$Python, [string[]]$ExpectedTargets) {
    $actual = @(Get-ManagedOfficialGuideTargets $Repo $Python)
    $actualText = (@($actual | Sort-Object) -join "`n")
    $expectedText = (@($ExpectedTargets | Sort-Object) -join "`n")
    if ($actualText -cne $expectedText) {
        Stop-Release "$Repo does not declare the same managed official-guide targets as the established release source."
    }
}

function Assert-ForbiddenGuidesAbsent([string]$Repo, [string]$Label, [string[]]$ForbiddenGuides) {
    $present = @($ForbiddenGuides | Where-Object { Test-Path -LiteralPath (Join-Path $Repo $_) })
    if ($present.Count) { Stop-Release "$Label contains prohibited MiniMax guide file(s): $($present -join ', ')" }
    $staged = Get-GitText $Repo $Label @('diff', '--cached', '--name-only', '--')
    $stagedGuides = @($ForbiddenGuides | Where-Object { $staged -split "`n" -contains $_ })
    if ($stagedGuides.Count) { Stop-Release "$Label has prohibited MiniMax guide file(s) staged: $($stagedGuides -join ', ')" }
}

function Get-ProjectVersion([string]$Repo) {
    $versions = foreach ($entry in $VersionFiles) {
        $path = Join-Path $Repo $entry.Path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Stop-Release "Version source is missing: $path" }
        $content = [System.IO.File]::ReadAllText($path)
        $matches = [regex]::Matches($content, $entry.Pattern)
        if ($matches.Count -ne 1) { Stop-Release "Expected exactly one authoritative version field in $path; found $($matches.Count)." }
        $match = $matches[0]
        if ($entry.Path -eq 'pyproject.toml') { [regex]::Match($match.Value, '"([^"]+)"').Groups[1].Value }
        else { [regex]::Matches($match.Value, '"([^"]+)"')[-1].Groups[1].Value }
    }
    $unique = @($versions | Select-Object -Unique)
    if ($unique.Count -ne 1) { Stop-Release "Version fields disagree in the development repository: $($unique -join ', ')" }
    return $unique[0]
}

function Set-ProjectVersion([string]$Repo, [string]$NewVersion) {
    $updates = @()
    foreach ($entry in $VersionFiles) {
        $path = Join-Path $Repo $entry.Path
        $content = [System.IO.File]::ReadAllText($path)
        $matches = [regex]::Matches($content, $entry.Pattern)
        if ($matches.Count -ne 1) { Stop-Release "Expected exactly one authoritative version field in $path; found $($matches.Count)." }
        $replacement = [string]::Format($entry.Replacement, $NewVersion)
        $updated = $content.Substring(0, $matches[0].Index) + $replacement + $content.Substring($matches[0].Index + $matches[0].Length)
        if ($updated -eq $content) { Stop-Release "Version update made no change in $path" }
        $updates += [pscustomobject]@{ Path = $path; Content = $updated }
    }
    foreach ($update in $updates) {
        [System.IO.File]::WriteAllText($update.Path, $update.Content, [System.Text.UTF8Encoding]::new($false))
    }
}

function Get-StatusPaths([string]$Repo, [string]$Label) {
    $status = Get-GitText $Repo $Label @('status', '--porcelain')
    if (-not $status) { return @() }
    return @($status -split "`n" | Where-Object { $_ } | ForEach-Object { $_.Substring(2).Trim() })
}

function Test-ReleaseSourceMarker([string]$Repo, [string]$Version) {
    $body = Get-GitText $Repo 'Development repository' @('show', '-s', '--format=%B', 'HEAD')
    return $body -match "(?m)^MiniConstruct-Release-Version: $([regex]::Escape($Version))$"
}

function Get-GitFileText([string]$Repo, [string]$Revision, [string]$Path) {
    $result = Invoke-Git -Repo $Repo -Label 'Development repository' -Arguments @('show', "${Revision}:$Path")
    return $result.Output
}

function Normalize-ReleaseText([string]$Text) {
    # Git blobs and Windows working-tree files may represent the same text with
    # different line endings. Release validation cares about content, not EOL style.
    return (($Text -replace "`r`n", "`n") -replace "`r", "`n")
}

function Test-ExactVersionBump([string]$Repo, [string]$Version, [string[]]$DirtyPaths) {
    $known = @($VersionFiles | ForEach-Object { $_.Path })

    if (
        @($DirtyPaths | Sort-Object -Unique).Count -ne $known.Count -or
        @($DirtyPaths | Where-Object { $_ -notin $known }).Count
    ) {
        return $false
    }

    foreach ($entry in $VersionFiles) {
        $working = Normalize-ReleaseText (
            [System.IO.File]::ReadAllText((Join-Path $Repo $entry.Path))
        )
        $head = Normalize-ReleaseText (
            Get-GitFileText $Repo 'HEAD' $entry.Path
        )
        $indexed = Normalize-ReleaseText (
            Get-GitFileText $Repo '' $entry.Path
        )

        $matches = [regex]::Matches($head, $entry.Pattern)
        if ($matches.Count -ne 1) {
            return $false
        }

        $replacement = [string]::Format($entry.Replacement, $Version)
        $expected = (
            $head.Substring(0, $matches[0].Index) +
            $replacement +
            $head.Substring($matches[0].Index + $matches[0].Length)
        )

        if ($working -cne $expected) {
            return $false
        }

        if ($indexed -cne $head -and $indexed -cne $expected) {
            return $false
        }
    }

    return $true
}

function Assert-DevelopmentPreparationState([string]$Repo, [string]$Version, [switch]$AllowCorrection) {
    $actual = Get-ProjectVersion $Repo
    $paths = @(Get-StatusPaths $Repo 'Development repository')
    $known = @($VersionFiles | ForEach-Object { $_.Path })
    if ($actual -ne $Version) {
        if ($paths.Count) { Stop-Release "Development repository is version $actual and has uncommitted changes; start from a clean repository." }
        return 'clean-old-version'
    }
    $automaticBump = Test-ExactVersionBump $Repo $Version $paths
    if ($paths.Count -and -not $automaticBump -and -not $AllowCorrection) {
        Stop-Release "Development repository has target version $Version but changes beyond the exact version substitutions. Re-run with -RefreshDevelopmentSource only after intentionally reviewing those corrections."
    }
    if ($paths.Count -and -not $automaticBump) { return 'intentional-correction' }
    if ($paths.Count) { return 'version-bump-pending' }
    if (Test-ReleaseSourceMarker $Repo $Version) { return 'established-source' }
    return 'target-version-unmarked'
}

function Assert-PreparedGitHubState([string]$Repo, [string]$ExpectedTree) {
    $unstaged = (Invoke-Git -Repo $Repo -Label 'GitHub repository' -Arguments @('diff', '--quiet') -AllowFailure).ExitCode
    if ($unstaged -ne 0) { Stop-Release 'GitHub repository has unstaged working-tree changes; it is not a resumable prepared snapshot.' }
    $untracked = Get-GitText $Repo 'GitHub repository' @('ls-files', '--others', '--exclude-standard')
    if ($untracked) { Stop-Release "GitHub repository has untracked files; it is not a resumable prepared snapshot:`n$untracked" }
    $status = Get-GitText $Repo 'GitHub repository' @('status', '--porcelain')
    if (-not $status) { return $false }
    $indexTree = Get-GitText $Repo 'GitHub repository' @('write-tree')
    if ($indexTree -ne $ExpectedTree) { Stop-Release "GitHub repository has staged changes that do not equal the public staging tree ($indexTree != $ExpectedTree)." }
    Write-ReleaseInfo 'GitHub repository contains the exact previously prepared staging snapshot and may be resumed.'
    return $true
}

function Get-RemoteTagCommit([string]$Repo, [string]$Tag) {
    # ls-remote is authoritative for the remote; a local show-ref is not proof
    # that publication succeeded. An annotated tag has a peeled ^{} line.
    $listing = Invoke-External -Label "Inspect remote target tag $Tag" -FilePath 'git' -Arguments @('-C', $Repo, 'ls-remote', '--tags', 'origin', "refs/tags/$Tag", "refs/tags/$Tag^{}")
    $direct = $null; $peeled = $null
    foreach ($line in @($listing.Output -split "`r?`n" | Where-Object { $_ })) {
        $parts = $line -split "`t", 2
        if ($parts.Count -ne 2) { Stop-Release "Could not parse remote target tag inspection for $Tag." }
        if ($parts[1] -eq "refs/tags/$Tag") { $direct = $parts[0] }
        elseif ($parts[1] -eq "refs/tags/$Tag^{}") { $peeled = $parts[0] }
    }
    if (-not $direct) { return [pscustomobject]@{ Exists = $false; Commit = $null } }
    return [pscustomobject]@{ Exists = $true; Commit = if ($peeled) { $peeled } else { $direct } }
}

function Get-GitHubPublicationState([string]$Repo, [string]$Tag, [string]$Version, [string]$ExpectedTree) {
    $localTag = Invoke-Git -Repo $Repo -Label 'Inspect local GitHub target tag' -Arguments @('show-ref', '--verify', '--quiet', "refs/tags/$Tag") -AllowFailure
    $remoteTag = Get-RemoteTagCommit $Repo $Tag
    if ($localTag.ExitCode -ne 0) {
        if ($remoteTag.Exists) { Stop-Release "Target tag $Tag exists on origin but not as a local resumable release tag. Reconcile it manually." }
        return [pscustomobject]@{ State = 'normal'; ReleaseCommit = $null }
    }

    Assert-RepositoryClean $Repo 'GitHub repository'
    $tagType = Get-GitText $Repo 'GitHub repository' @('cat-file', '-t', "refs/tags/$Tag")
    $tagSubject = Get-GitText $Repo 'GitHub repository' @('for-each-ref', '--format=%(contents:subject)', "refs/tags/$Tag")
    if ($tagType -ne 'tag' -or $tagSubject -cne "MiniConstruct $Version") {
        Stop-Release "Existing GitHub tag $Tag is not the exact annotated release tag for version $Version. Reconcile it manually."
    }
    $releaseCommit = Get-GitText $Repo 'GitHub repository' @('rev-parse', "$Tag^{}")
    $githubHead = Get-GitText $Repo 'GitHub repository' @('rev-parse', 'HEAD')
    if ($githubHead -ne $releaseCommit) { Stop-Release "Existing GitHub tag $Tag does not peel to local master HEAD. Reconcile it manually." }
    $commitSubject = Get-GitText $Repo 'GitHub repository' @('show', '-s', '--format=%s', $releaseCommit)
    if ($commitSubject -cne "Release MiniConstruct $Version") { Stop-Release "Existing GitHub release commit does not have the expected release subject for $Version. Reconcile it manually." }
    $releaseTree = Get-GitText $Repo 'GitHub repository' @('rev-parse', "$releaseCommit^{tree}")
    if ($releaseTree -ne $ExpectedTree) { Stop-Release "Existing GitHub tag $Tag tree differs from the expected public staging tree. Reconcile it manually." }
    if ((Get-ProjectVersion $Repo) -ne $Version) { Stop-Release "Existing GitHub release commit does not carry requested version $Version." }

    $originHead = Get-GitText $Repo 'GitHub repository' @('rev-parse', 'origin/master')
    if ($remoteTag.Exists) {
        if ($remoteTag.Commit -ne $releaseCommit -or $originHead -ne $releaseCommit) {
            Stop-Release "Remote master/tag state for $Tag is partial, divergent, or ambiguous. Reconcile it manually."
        }
        Write-ReleaseInfo "Remote master and $Tag already publish the exact prepared release; treating the prior atomic push as successful."
        return [pscustomobject]@{ State = 'already-pushed'; ReleaseCommit = $releaseCommit }
    }
    $parentLine = Get-GitText $Repo 'GitHub repository' @('rev-list', '--parents', '-n', '1', $releaseCommit)
    $releaseParents = @($parentLine -split '\s+' | Where-Object { $_ })
    if ($releaseParents.Count -ne 2) { Stop-Release "Release commit $releaseCommit does not have exactly one parent; its exact parent state is ambiguous. Reconcile it manually." }
    $releaseParent = $releaseParents[1]
    $originHead = Get-GitText $Repo 'GitHub repository' @('rev-parse', 'origin/master')
    if ($originHead -ne $releaseParent) { Stop-Release "origin/master is not the exact parent of the pending release commit; the state is partial, divergent, or ambiguous. Reconcile it manually." }
    Write-ReleaseInfo "Previously prepared local release $Tag is exact and push-pending; resuming its atomic publication."
    return [pscustomobject]@{ State = 'push-pending'; ReleaseCommit = $releaseCommit }
}

function Assert-NoCachedConflictMarkers([string]$Repo) {
    $scan = Invoke-Git -Repo $Repo -Label 'Prepared GitHub conflict-marker scan' -Arguments @('grep', '--cached', '-n', '-E', '^(<<<<<<<|=======|>>>>>>>)') -AllowFailure
    if ($scan.ExitCode -eq 0) { Stop-Release "Prepared GitHub snapshot contains merge-conflict markers:`n$($scan.Output.Trim())" }
    if ($scan.ExitCode -eq 1) { return }
    Stop-Release "Prepared GitHub conflict-marker scan failed with exit code $($scan.ExitCode):`n$($scan.Output.Trim())"
}

function Get-Python([string]$DevRoot, [string]$Override) {
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override -PathType Leaf)) { Stop-Release "-PythonExe does not exist: $Override" }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    $venvPython = Join-Path $DevRoot '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) { return $venvPython }
    Test-Command 'python'
    return 'python'
}

function Invoke-Checked([string]$Label, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    $result = Invoke-External -Label $Label -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory
    if ($result.Output) { Write-Host $result.Output }
}

function Invoke-Tests([string]$Repo, [string]$Python, [string]$Label) {
    Write-ReleaseInfo "Running Python, JavaScript, compile, and whitespace checks in $Label."
    Invoke-Checked "$Label Python tests" $Python @('-m', 'pytest') $Repo
    $jsTests = @(Get-ChildItem -LiteralPath (Join-Path $Repo 'tests/js') -Filter '*.test.mjs' -File | ForEach-Object { $_.FullName })
    if (-not $jsTests.Count) { Stop-Release "$Label has no JavaScript tests under tests/js." }
    Invoke-Checked "$Label JavaScript tests" 'node' (@('--test') + $jsTests) $Repo
    Invoke-Checked "$Label compile check" $Python @('-m', 'compileall', '-q', 'miniconstruct') $Repo
    Invoke-Checked "$Label whitespace check" 'git' @('-C', $Repo, 'diff', '--check')
}

function Invoke-PublicTests([string]$Repo, [string]$Python, [string[]]$ForbiddenGuides) {
    $acquisitionModule = Join-Path $Repo 'miniconstruct/h3/guide_acquisition.py'
    if (-not (Test-Path -LiteralPath $acquisitionModule -PathType Leaf)) {
        Stop-Release "Public staging guide-acquisition tooling is missing: $acquisitionModule"
    }
    Assert-ForbiddenGuidesAbsent $Repo 'Public staging repository' $ForbiddenGuides
    $guidePaths = @($ForbiddenGuides | ForEach-Object { Join-Path $Repo $_ })
    $indexBefore = Get-GitText $Repo 'Public staging repository before tests' @('write-tree')
    $statusBefore = Get-GitText $Repo 'Public staging repository before tests' @('status', '--porcelain')
    try {
        Invoke-Checked 'Public staging guide acquisition' $Python @('-B', '-m', 'miniconstruct.h3.guide_acquisition') $Repo
        Invoke-Tests $Repo $Python 'public staging repository'
        Invoke-Checked 'Public staging prepared-index whitespace check' 'git' @('-C', $Repo, 'diff', '--cached', '--check')
    }
    finally {
        foreach ($path in $guidePaths) {
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        }
        Assert-ForbiddenGuidesAbsent $Repo 'Public staging repository after test cleanup' $ForbiddenGuides
        $indexAfter = Get-GitText $Repo 'Public staging repository after test cleanup' @('write-tree')
        $statusAfter = Get-GitText $Repo 'Public staging repository after test cleanup' @('status', '--porcelain')
        if ($indexAfter -ne $indexBefore -or $statusAfter -ne $statusBefore) {
            Stop-Release 'Public guide testing changed the prepared staging index or status; the merge remains uncommitted for inspection.'
        }
    }
}

function Complete-PublicMerge([string]$Repo, [string]$ExpectedMergeHead) {
    if (-not (Get-MergeInProgress $Repo 'Public staging repository')) { return }
    $actualMergeHead = Get-GitText $Repo 'Public staging repository' @('rev-parse', '-q', '--verify', 'MERGE_HEAD')
    if ($actualMergeHead -ne $ExpectedMergeHead) {
        Stop-Release "Public staging has an unrelated merge in progress ($actualMergeHead, expected $ExpectedMergeHead). It was left untouched."
    }
    $unmerged = Get-GitText $Repo 'Public staging repository' @('diff', '--name-only', '--diff-filter=U')
    if ($unmerged) { Stop-Release "Public staging merge still has conflicts:`n$unmerged`nResolve and stage them; do not abort the merge." }
    $unstaged = (Invoke-Git -Repo $Repo -Label 'Public staging repository' -Arguments @('diff', '--quiet') -AllowFailure).ExitCode
    if ($unstaged -ne 0) { Stop-Release 'Public staging merge has unstaged edits. Stage the resolved merge before rerunning.' }
    Invoke-Checked 'Complete resolved public staging merge' 'git' @('-C', $Repo, 'commit', '--no-edit')
}

function Get-BlobId([string]$Repo, [string]$Label, [string]$Revision, [string]$Path) {
    $result = Invoke-Git -Repo $Repo -Label $Label -Arguments @('rev-parse', "${Revision}:$Path") -AllowFailure
    if ($result.ExitCode -ne 0) { return $null }
    return $result.Output.Trim()
}

function Assert-RemoteOnlyChangesMatchStaging([string]$GitHubRoot, [string]$StagingRoot, [string]$StagingHead) {
    $remoteTagListing = (Invoke-External -Label 'List public tags from origin' -FilePath 'git' -Arguments @('-C', $GitHubRoot, 'ls-remote', '--tags', 'origin', 'refs/tags/v*')).Output
    $remoteTags = @(
        $remoteTagListing -split "`n" |
        ForEach-Object { ($_ -split "`t")[-1] -replace '^refs/tags/', '' } |
        Where-Object { $_ -match '^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$' }
    )
    $tags = Get-GitText $GitHubRoot 'GitHub repository' @('tag', '--merged', 'origin/master', '--sort=-version:refname')
    $previousTag = @($tags -split "`n" | Where-Object { $_ -in $remoteTags } | Select-Object -First 1)
    if (-not $previousTag) { Stop-Release 'Could not identify the previous public semantic-version tag on origin/master; refusing to overwrite remote-only changes.' }
    $paths = Get-GitText $GitHubRoot 'GitHub repository' @('diff', '--name-only', "$previousTag..origin/master")
    $conflicts = @()
    foreach ($path in @($paths -split "`n" | Where-Object { $_ })) {
        $remoteBlob = Get-BlobId $GitHubRoot 'GitHub repository' 'origin/master' $path
        $stagingBlob = Get-BlobId $StagingRoot 'Public staging repository' $StagingHead $path
        if ($remoteBlob -ne $stagingBlob) { $conflicts += $path }
    }
    if ($conflicts.Count) {
        Stop-Release "GitHub-only changes since $previousTag differ from public staging. Reconcile these paths in development before releasing:`n$($conflicts -join "`n")"
    }
    Write-ReleaseInfo "Remote-only public changes since $previousTag match public staging."
}

function Confirm([string]$Expected) {
    $answer = Read-Host "Type $Expected to continue"
    if ($answer -cne $Expected) { Stop-Release 'Confirmation did not match. Prepared state was left unchanged.' }
}

try {
    Test-Command 'git'; Test-Command 'node'
    $scriptRoot = Split-Path -Parent $PSCommandPath
    if (-not $DevRepo) { $DevRepo = $scriptRoot }
    $DevRepo = Resolve-RepositoryPath $DevRepo 'Development repository'
    if (-not $PublicRepo) { $PublicRepo = Join-Path (Split-Path -Parent $DevRepo) ((Split-Path -Leaf $DevRepo) + '-public') }
    if (-not $GitHubRepo) { $GitHubRepo = Join-Path (Split-Path -Parent $DevRepo) ((Split-Path -Leaf $DevRepo) + '-GitHub') }
    $PublicRepo = Resolve-RepositoryPath $PublicRepo 'Public staging repository'; $GitHubRepo = Resolve-RepositoryPath $GitHubRepo 'GitHub repository'
    if ($ReleaseNotesPath) {
        if (-not (Test-Path -LiteralPath $ReleaseNotesPath -PathType Leaf)) { Stop-Release "Release notes file does not exist: $ReleaseNotesPath" }
        $ReleaseNotesPath = (Resolve-Path -LiteralPath $ReleaseNotesPath).Path
    }
    $python = Get-Python $DevRepo $PythonExe; $tag = "v$Version"
    $ForbiddenGuides = @(Get-ManagedOfficialGuideTargets $DevRepo $python)
    Assert-GitRepository $DevRepo 'Development repository' $DevBranch; Assert-GitRepository $PublicRepo 'Public staging repository' 'public-release'; Assert-GitRepository $GitHubRepo 'GitHub repository' 'master'
    $devState = Assert-DevelopmentPreparationState $DevRepo $Version -AllowCorrection:$RefreshDevelopmentSource
    Assert-RepositoryClean $PublicRepo 'Public staging repository' -PermitResolvedMerge
    Assert-ForbiddenGuidesAbsent $PublicRepo 'Public staging repository' $ForbiddenGuides; Assert-ForbiddenGuidesAbsent $GitHubRepo 'GitHub repository' $ForbiddenGuides
    if ($DryRun) { Assert-RepositoryClean $GitHubRepo 'GitHub repository' }
    foreach ($repo in @($DevRepo, $PublicRepo)) {
        $existing = Invoke-Git -Repo $repo -Label 'Tag preflight' -Arguments @('show-ref', '--verify', '--quiet', "refs/tags/$tag") -AllowFailure
        if ($existing.ExitCode -eq 0) { Stop-Release "Target tag $tag already exists in $repo." }
    }
    $possibleGitHubResume = (Invoke-Git -Repo $GitHubRepo -Label 'GitHub target tag preflight' -Arguments @('show-ref', '--verify', '--quiet', "refs/tags/$tag") -AllowFailure).ExitCode -eq 0
    Write-ReleaseInfo "Preflight passed; development state is $devState."
    if ($DryRun) {
        if ($possibleGitHubResume) { Write-ReleaseInfo "DRY RUN: found a possible local GitHub resume tag $tag; this preflight-only run does not prove it is resumable." }
        Write-ReleaseInfo 'DRY RUN PREFLIGHT PASSED: no files, Git refs, indexes, worktrees, or remotes were changed. DryRun stops before version mutation, full tests, merges, public snapshot preparation, and GitHub commit/tag/push; it does not simulate the full release sequence.'
        exit 0
    }

    if ($devState -eq 'clean-old-version') { Set-ProjectVersion $DevRepo $Version; $devState = 'version-bump-pending' }
    Invoke-Tests $DevRepo $python 'development repository'
    $devStateAfterTests = Assert-DevelopmentPreparationState $DevRepo $Version -AllowCorrection:$RefreshDevelopmentSource
    $testedDevTree = $null
    if ($devStateAfterTests -eq 'version-bump-pending') {
        Confirm "DEV $Version"
        Invoke-Checked 'Stage known version fields' 'git' (@('-C', $DevRepo, 'add') + @($VersionFiles | ForEach-Object { $_.Path }))
        $testedDevTree = Get-GitText $DevRepo 'Development repository' @('write-tree')
        Invoke-Checked 'Create development release-source commit' 'git' @('-C', $DevRepo, 'commit', '-m', "Release MiniConstruct $Version", '-m', "MiniConstruct-Release-Version: $Version")
    }
    elseif ($devStateAfterTests -eq 'intentional-correction') {
        Confirm "SOURCE $Version"
        Invoke-Checked 'Stage reviewed corrected release source' 'git' @('-C', $DevRepo, 'add', '-A')
        $testedDevTree = Get-GitText $DevRepo 'Development repository' @('write-tree')
        Invoke-Checked 'Create refreshed development release-source commit' 'git' @('-C', $DevRepo, 'commit', '-m', "Release MiniConstruct $Version source refresh", '-m', "MiniConstruct-Release-Version: $Version")
    }
    elseif ($devStateAfterTests -eq 'target-version-unmarked') {
        Confirm "SOURCE $Version"
        $testedDevTree = Get-GitText $DevRepo 'Development repository' @('write-tree')
        Invoke-Checked 'Mark already-versioned tested source' 'git' @('-C', $DevRepo, 'commit', '--allow-empty', '-m', "Release MiniConstruct $Version source marker", '-m', "MiniConstruct-Release-Version: $Version")
    }
    if (-not $testedDevTree) { $testedDevTree = Get-GitText $DevRepo 'Development repository' @('write-tree') }
    if (-not (Test-ReleaseSourceMarker $DevRepo $Version)) { Stop-Release 'Development source was not explicitly established as a release source.' }
    Assert-RepositoryClean $DevRepo 'Development repository'
    $developmentHead = Get-GitText $DevRepo 'Development repository' @('rev-parse', 'HEAD')
    $developmentTree = Get-GitText $DevRepo 'Development repository' @('rev-parse', 'HEAD^{tree}')
    if ($developmentTree -ne $testedDevTree) { Stop-Release 'Development commit tree differs from the tree that was tested/prepared.' }

    if (Get-MergeInProgress $PublicRepo 'Public staging repository') {
        $mergeHead = Get-GitText $PublicRepo 'Public staging repository' @('rev-parse', '-q', '--verify', 'MERGE_HEAD')
        if ($mergeHead -ne $developmentHead) { Stop-Release "Public staging MERGE_HEAD is $mergeHead, not the established tested source $developmentHead. It was left untouched." }
    }
    else {
        $alreadyMerged = (Invoke-Git -Repo $PublicRepo -Label 'Public staging repository' -Arguments @('merge-base', '--is-ancestor', $developmentHead, 'HEAD') -AllowFailure).ExitCode -eq 0
        if (-not $alreadyMerged) {
            $merge = Invoke-Git -Repo $PublicRepo -Label 'Prepare exact tested development SHA merge' -Arguments @('merge', '--no-ff', '--no-commit', $developmentHead) -AllowFailure
            if ($merge.ExitCode -ne 0) {
                $unmerged = Get-GitText $PublicRepo 'Public staging repository' @('diff', '--name-only', '--diff-filter=U')
                if ($unmerged) { Stop-Release "Public staging merge has conflicts and remains in progress. Resolve and stage these paths, then rerun:`n$unmerged" }
                Stop-Release "Public staging merge failed without a resumable merge state:`n$($merge.Output)"
            }
        }
    }
    if (Get-MergeInProgress $PublicRepo 'Public staging repository') {
        $mergeHead = Get-GitText $PublicRepo 'Public staging repository' @('rev-parse', '-q', '--verify', 'MERGE_HEAD')
        if ($mergeHead -ne $developmentHead) { Stop-Release 'Public staging merge target changed unexpectedly; it was left untouched.' }
    }
    if ((Get-ProjectVersion $PublicRepo) -ne $Version) { Stop-Release "Public staging version does not equal requested version $Version after merge resolution." }
    Assert-ManagedOfficialGuideTargets $PublicRepo $python $ForbiddenGuides
    Invoke-PublicTests $PublicRepo $python $ForbiddenGuides
    Complete-PublicMerge $PublicRepo $developmentHead
    Assert-RepositoryClean $PublicRepo 'Public staging repository'
    $stagingHead = Get-GitText $PublicRepo 'Public staging repository' @('rev-parse', 'HEAD'); $stagingTree = Get-GitText $PublicRepo 'Public staging repository' @('rev-parse', 'HEAD^{tree}')

    Invoke-Checked 'Fetch GitHub master' 'git' @('-C', $GitHubRepo, 'fetch', 'origin', 'master')
    $publication = Get-GitHubPublicationState $GitHubRepo $tag $Version $stagingTree
    if ($publication.State -eq 'normal') {
        $preparedGitHub = Assert-PreparedGitHubState $GitHubRepo $stagingTree
        $githubHeadBefore = Get-GitText $GitHubRepo 'GitHub repository' @('rev-parse', 'HEAD')
        $originHead = Get-GitText $GitHubRepo 'GitHub repository' @('rev-parse', 'origin/master')
        if ($preparedGitHub) {
            if ($githubHeadBefore -ne $originHead) { Stop-Release 'origin/master changed since the GitHub snapshot was prepared; it was left staged for review.' }
        }
        else {
            $localBehindOrEqual = (Invoke-Git -Repo $GitHubRepo -Label 'GitHub repository' -Arguments @('merge-base', '--is-ancestor', 'HEAD', 'origin/master') -AllowFailure).ExitCode -eq 0
            if (-not $localBehindOrEqual) { Stop-Release 'Local GitHub master has commits not present on origin/master. Reconcile it manually; this tool will not overwrite it.' }
            Invoke-Checked 'Fast-forward GitHub master to origin/master' 'git' @('-C', $GitHubRepo, 'merge', '--ff-only', 'origin/master')
        }
        Assert-RemoteOnlyChangesMatchStaging $GitHubRepo $PublicRepo $stagingHead
        if (-not $preparedGitHub) {
            $exportTool = Join-Path $PublicRepo 'tools/export_public_snapshot.py'
            if (-not (Test-Path -LiteralPath $exportTool -PathType Leaf)) { Stop-Release "Public staging safety exporter is missing: $exportTool" }
            Invoke-Checked 'Export committed public staging tree into clean GitHub clone' $python @($exportTool, $GitHubRepo)
            $unexpectedUntracked = Get-GitText $GitHubRepo 'Prepared GitHub repository' @('ls-files', '--others', '--exclude-standard')
            if ($unexpectedUntracked) { Stop-Release "Tree export left unexpected untracked files in the GitHub clone:`n$unexpectedUntracked" }
            Invoke-Checked 'Stage exported GitHub snapshot' 'git' @('-C', $GitHubRepo, 'add', '-A')
        }
        Assert-ManagedOfficialGuideTargets $GitHubRepo $python $ForbiddenGuides
        Assert-ForbiddenGuidesAbsent $GitHubRepo 'Prepared GitHub repository' $ForbiddenGuides
        Invoke-Checked 'Check prepared GitHub snapshot whitespace' 'git' @('-C', $GitHubRepo, 'diff', '--cached', '--check')
        Assert-NoCachedConflictMarkers $GitHubRepo
        $githubTree = Get-GitText $GitHubRepo 'Prepared GitHub repository' @('write-tree')
        if ($githubTree -ne $stagingTree) { Stop-Release "Prepared GitHub index tree does not equal public staging tree ($githubTree != $stagingTree)." }
        if ((Get-ProjectVersion $GitHubRepo) -ne $Version) { Stop-Release "Prepared GitHub tree does not carry requested version $Version." }
        $githubHead = Get-GitText $GitHubRepo 'GitHub repository' @('rev-parse', 'HEAD'); $identityName = Get-GitText $GitHubRepo 'GitHub repository' @('config', 'user.name'); $identityEmail = Get-GitText $GitHubRepo 'GitHub repository' @('config', 'user.email')
        if (-not $identityName -or -not $identityEmail) { Stop-Release 'GitHub repository has no configured user.name/user.email; configure the intended public identity locally and rerun.' }
        $remoteUrl = Get-GitText $GitHubRepo 'GitHub repository' @('remote', 'get-url', 'origin'); $stat = Get-GitText $GitHubRepo 'Prepared GitHub repository' @('diff', '--cached', '--stat')
        Write-Host "`nPublic release summary`n  Version: $Version`n  Tested development SHA: $developmentHead`n  Public staging SHA/tree: $stagingHead / $stagingTree`n  GitHub current HEAD: $githubHead`n  Target tag: $tag`n  Remote: $remoteUrl`n  Git identity: $identityName <$identityEmail>`n  Staged diff:`n$stat"
        Confirm "RELEASE $Version"
        Invoke-Checked 'Create public GitHub release commit' 'git' @('-C', $GitHubRepo, 'commit', '-m', "Release MiniConstruct $Version")
        Invoke-Checked 'Create annotated public tag' 'git' @('-C', $GitHubRepo, 'tag', '-a', $tag, '-m', "MiniConstruct $Version")
        Invoke-Checked 'Atomically push public master and tag' 'git' @('-C', $GitHubRepo, 'push', '--atomic', 'origin', 'master', $tag)
    }
    elseif ($publication.State -eq 'push-pending') {
        Assert-ManagedOfficialGuideTargets $GitHubRepo $python $ForbiddenGuides
        Assert-ForbiddenGuidesAbsent $GitHubRepo 'GitHub push-pending repository' $ForbiddenGuides
        Assert-RemoteOnlyChangesMatchStaging $GitHubRepo $PublicRepo $stagingHead
        Invoke-Checked 'Resume atomic push of existing public master and tag' 'git' @('-C', $GitHubRepo, 'push', '--atomic', 'origin', 'master', $tag)
    }
    elseif ($publication.State -eq 'already-pushed') {
        Assert-ManagedOfficialGuideTargets $GitHubRepo $python $ForbiddenGuides
        Assert-ForbiddenGuidesAbsent $GitHubRepo 'GitHub already-published repository' $ForbiddenGuides
        Write-ReleaseInfo 'Skipping Git push because the exact master and tag are already published.'
    }
    else { Stop-Release "Unknown GitHub publication state: $($publication.State)" }
    if ($ReleaseNotesPath -and (Get-Command 'gh' -ErrorAction SilentlyContinue)) {
        $gh = Invoke-External -Label 'Create GitHub Release object' -FilePath 'gh' -Arguments @('release', 'create', $tag, '--title', "MiniConstruct $Version", '--notes-file', $ReleaseNotesPath, '--latest', '--verify-tag') -WorkingDirectory $GitHubRepo -AllowFailure
        if ($gh.ExitCode -ne 0) { Write-Warning "Git ref publication is confirmed; only the GitHub Release object failed. Recover with: gh release create $tag --title 'MiniConstruct $Version' --notes-file '$ReleaseNotesPath' --latest --verify-tag" }
    }
    elseif ($ReleaseNotesPath) { Write-ReleaseInfo "Git ref publication is confirmed. gh is unavailable; create the GitHub Release later for $tag using $ReleaseNotesPath." }
    else { Write-ReleaseInfo "Git ref publication is confirmed. No GitHub Release object was requested (supply -ReleaseNotesPath to create one)." }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
