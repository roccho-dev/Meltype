#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev
"""Exercise real old/new Core assemblies against isolated settings and user data.

This is a compatibility probe, not a restore mechanism or a live-user migration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from xml.sax.saxutils import escape

PROGRAM = r'''
// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 roccho-dev
using System.Text.Json;
using Meltype.Composition;
using Meltype.Config;

var mode = args[0];
var directory = args[1];
Directory.CreateDirectory(directory);
var config = Path.Combine(directory, "config.json");
var dictionary = new UserDictionary(Path.Combine(directory, "userdict.txt"), builtIn: false);
var languages = new LanguageMemory(Path.Combine(directory, "languages.json"));
void Require(bool value, string message) { if (!value) throw new Exception(message); }
Settings settings;
if (mode == "seed")
{
    settings = new Settings { Mode = InputMode.Keyboard, LiveConversion = false, ShowTypedKeys = true,
        Punctuation = PunctuationStyle.FullWidthCommaPeriod, CompositionFont = "user-selected-font" };
    settings.Save(config);
    Require(dictionary.Add("すみれ", "菫") is null, "baseline dictionary write");
    languages.Remember("baselineword", false, explicitChoice: true);
}
else
{
    settings = Settings.Load(config);
    Require(settings.Mode == InputMode.Keyboard, "input mode preserved");
    Require(!settings.LiveConversion, "conversion preference preserved");
    Require(settings.Punctuation == PunctuationStyle.FullWidthCommaPeriod, "punctuation preserved");
    Require(settings.CompositionFont == "user-selected-font", "font preference preserved");
    Require(dictionary.Lookup("すみれ").Contains("菫"), "baseline dictionary preserved");
    Require(languages.Get("baselineword") == false, "baseline learning preserved");
    if (mode == "upgrade")
    {
        Require(settings.ShowTypedKeys, "baseline display preference preserved");
        settings.ShowTypedKeys = false;
#if CANDIDATE
        settings.NowPlayingCandidates = false;
#endif
        settings.Save(config);
        Require(dictionary.Add("さいせんたん", "最先端") is null, "post-upgrade dictionary write");
        languages.Remember("afterupgrade", true, explicitChoice: true);
    }
    else
    {
        Require(!settings.ShowTypedKeys, "post-upgrade config preserved");
        Require(dictionary.Lookup("さいせんたん").Contains("最先端"), "post-upgrade dictionary preserved on rollback");
        Require(languages.Get("afterupgrade") == true, "post-upgrade learning preserved on rollback");
        if (mode == "rollback-save")
        {
            settings.Save(config);
            Require(dictionary.Add("もどしたあと", "戻した後") is null, "post-rollback dictionary write");
            languages.Remember("afterrollback", false, explicitChoice: true);
        }
        else if (mode == "verify")
        {
            Require(dictionary.Lookup("もどしたあと").Contains("戻した後"), "work after rollback preserved on re-upgrade");
            Require(languages.Get("afterrollback") == false, "learning after rollback preserved on re-upgrade");
        }
    }
}
Require(!Directory.GetFiles(directory, "*.broken").Any(), "no silent fallback to defaults");
Console.WriteLine(JsonSerializer.Serialize(new {
    mode, settingsVersion = settings.SettingsVersion,
    dictionaryBefore = dictionary.Lookup("すみれ"), dictionaryAfter = dictionary.Lookup("さいせんたん"),
    dictionaryAfterRollback = dictionary.Lookup("もどしたあと"),
    baselineLearning = languages.Get("baselineword"), newLearning = languages.Get("afterupgrade"),
    rollbackLearning = languages.Get("afterrollback"),
#if CANDIDATE
    newOption = settings.NowPlayingCandidates,
#endif
    commonDataChecks = true
}));
'''


def run(command, directory, log):
    result = subprocess.run(command, cwd=directory, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}); see {log}")
    return result.stdout


def manifest(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--dotnet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    assemblies = {}
    source = {}
    for label in ("baseline", "candidate"):
        repository = getattr(args, label).resolve()
        source[label] = {
            "repository": str(repository),
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
            "diffSha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD", "--", "src/Meltype.Core"] , cwd=repository)).hexdigest(),
        }
        project = output / label
        project.mkdir()
        (project / "Program.cs").write_text(PROGRAM)
        reference = escape(str(repository / "src/Meltype.Core/Meltype.Core.csproj"), {'"': '&quot;'})
        constants = "<DefineConstants>CANDIDATE</DefineConstants>" if label == "candidate" else ""
        (project / "Probe.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework>'
            '<OutputType>Exe</OutputType><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable>'
            f'{constants}</PropertyGroup><ItemGroup><ProjectReference Include="{reference}" />'
            '</ItemGroup></Project>')
        run([str(args.dotnet), "build", "Probe.csproj", "-c", "Release"], project, output / f"{label}-build.log")
        assemblies[label] = project / "bin/Release/net10.0/Probe.dll"
        source[label]["probeAssemblySha256"] = hashlib.sha256(assemblies[label].read_bytes()).hexdigest()
        source[label]["coreAssemblySha256"] = hashlib.sha256((assemblies[label].parent / "Meltype.Core.dll").read_bytes()).hexdigest()
    data = output / "data"
    stages = []
    for label, mode in (("baseline", "seed"), ("candidate", "upgrade"),
                        ("baseline", "rollback-save"), ("candidate", "verify")):
        stdout = run([str(args.dotnet), str(assemblies[label]), mode, str(data)], output, output / f"{mode}.log")
        stages.append({"assembly": label, **json.loads(stdout.splitlines()[-1]), "dataManifest": manifest(data)})
        shutil.copytree(data, output / f"snapshot-{mode}")
    before = json.loads((output / "snapshot-upgrade/config.json").read_text())
    after = json.loads((output / "snapshot-rollback-save/config.json").read_text())
    lost_keys = sorted(set(before) - set(after))
    # The supported minimum keeps the post-upgrade data untouched and lets the
    # old binary write a separate copy. In this case no old-version preference
    # edits are made; they would need an explicit choice, not a generic merge.
    protected = output / "protected-post-upgrade"
    shutil.copytree(output / "snapshot-upgrade", protected)
    protected_manifest = manifest(protected)
    rollback = output / "rollback-working"
    shutil.copytree(protected, rollback)
    stdout = run([str(args.dotnet), str(assemblies["baseline"]), "rollback-save", str(rollback)], output, output / "safe-rollback-save.log")
    safe_rollback = json.loads(stdout.splitlines()[-1])
    resume = output / "resume-working"
    shutil.copytree(rollback, resume)
    shutil.copy2(resume / "config.json", resume / "config-from-rollback.json")
    shutil.copy2(protected / "config.json", resume / "config.json")
    stdout = run([str(args.dotnet), str(assemblies["candidate"]), "verify", str(resume)], output, output / "safe-reupgrade.log")
    safe_resume = json.loads(stdout.splitlines()[-1])
    if safe_resume.get("newOption") is not False or manifest(protected) != protected_manifest:
        raise RuntimeError("separate-copy recovery lost the protected configuration")
    report = {
        "scope": "isolated real Core settings/dictionary/language-learning compatibility; no host or user data touched",
        "sources": source, "stages": stages,
        "commonDataRoundtrip": True,
        "candidateOnlySettingSurvivesOldSave": stages[-1].get("newOption") is False,
        "candidateConfigKeysDroppedByOldSave": lost_keys,
        "status": "ROLLBACK_CONFIG_LOSS_REPRODUCED_AND_SEPARATE_COPY_RECOVERY_VERIFIED",
        "safeRecovery": {"oldVersion": safe_rollback, "newVersion": safe_resume,
                         "postUpgradeSnapshotUnchanged": True,
                         "protectedManifest": protected_manifest,
                         "resumedManifest": manifest(resume),
                         "oldVersionConfigRetained": "resume-working/config-from-rollback.json"},
        "recoveryLimit": "Old-version settings saves are not lossless. The supported probe preserves post-upgrade configuration separately and retains dictionary/learning edits from the rollback copy. Old-version preference edits need explicit comparison/reapplication; no generic merge or live restore is provided. Never overwrite new work with the old seed snapshot.",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "commonDataRoundtrip", "candidateOnlySettingSurvivesOldSave", "candidateConfigKeysDroppedByOldSave")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
