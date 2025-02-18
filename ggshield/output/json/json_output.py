from typing import Any, Dict, List, Tuple

import click
from pygitguardian.client import VERSIONS
from pygitguardian.models import Match, PolicyBreak

from ggshield.filter import censor_content, leak_dictionary_by_ignore_sha
from ggshield.output.json.schemas import ExtendedMatch, JSONScanCollectionSchema
from ggshield.output.output_handler import OutputHandler
from ggshield.scan import Result
from ggshield.scan.scannable import ScanCollection
from ggshield.text_utils import Line
from ggshield.utils import Filemode, find_match_indices, get_lines_from_content


class JSONHandler(OutputHandler):
    def process_scan(
        self, scan: ScanCollection, top: bool = True
    ) -> Tuple[Dict[str, Any], int]:
        scan_dict: Dict[str, Any] = {
            "id": scan.id,
            "type": scan.type,
            "total_incidents": 0,
            "total_occurrences": 0,
        }
        return_code = 0

        if scan.extra_info:
            scan_dict["extra_info"] = scan.extra_info

        if top and scan.results:
            scan_dict["secrets_engine_version"] = VERSIONS.secrets_engine_version

        if scan.results:
            return_code = 1
            for result in scan.results:
                result_dict = self.process_result(result)
                scan_dict.setdefault("results", []).append(result_dict)
                scan_dict["total_incidents"] += result_dict["total_incidents"]
                scan_dict["total_occurrences"] += result_dict["total_occurrences"]

        if scan.scans:
            for inner_scan in scan.scans_with_results:
                inner_scan_dict, inner_return_code = self.process_scan(
                    inner_scan, top=False
                )
                scan_dict.setdefault("scans", []).append(inner_scan_dict)
                scan_dict["total_incidents"] += inner_scan_dict["total_incidents"]
                scan_dict["total_occurrences"] += inner_scan_dict["total_occurrences"]
                return_code = max(return_code, inner_return_code)

        if top:
            if self.output:
                with open(self.output, "w+") as f:
                    f.write(JSONScanCollectionSchema().dumps(scan_dict))
            else:
                click.echo(JSONScanCollectionSchema().dumps(scan_dict))

        return scan_dict, return_code

    def process_result(self, result: Result) -> Dict[str, Any]:
        result_dict: Dict[str, Any] = {
            "filename": result.filename,
            "mode": result.filemode.name,
            "incidents": [],
            "total_occurrences": 0,
            "total_incidents": 0,
        }
        content = result.content
        is_patch = result.filemode != Filemode.FILE

        if not self.show_secrets:
            content = censor_content(result.content, result.scan.policy_breaks)

        lines = get_lines_from_content(
            content, result.filemode, is_patch, self.show_secrets
        )
        sha_dict = leak_dictionary_by_ignore_sha(result.scan.policy_breaks)

        result_dict["total_incidents"] = len(sha_dict)

        for ignore_sha, policy_breaks in sha_dict.items():
            flattened_dict = self.flattened_policy_break(
                ignore_sha,
                policy_breaks,
                lines,
                is_patch,
            )
            result_dict["incidents"].append(flattened_dict)
            result_dict["total_occurrences"] += flattened_dict["total_occurrences"]

        return result_dict

    def flattened_policy_break(
        self,
        ignore_sha: str,
        policy_breaks: List[PolicyBreak],
        lines: List[Line],
        is_patch: bool,
    ) -> Dict[str, Any]:
        flattened_dict: Dict[str, Any] = {
            "occurrences": [],
            "ignore_sha": ignore_sha,
            "policy": policy_breaks[0].policy,
            "break_type": policy_breaks[0].break_type,
            "total_occurrences": len(policy_breaks),
        }

        if policy_breaks[0].validity:
            flattened_dict["validity"] = policy_breaks[0].validity

        for policy_break in policy_breaks:
            matches = JSONHandler.make_matches(policy_break.matches, lines, is_patch)
            flattened_dict["occurrences"].extend(matches)

        return flattened_dict

    @staticmethod
    def make_matches(
        matches: List[Match], lines: List[Line], is_patch: bool
    ) -> List[ExtendedMatch]:
        res = []
        for match in matches:
            if match.index_start is None or match.index_end is None:
                res.append(match)
                continue
            indices = find_match_indices(match, lines, is_patch)
            line_start = lines[indices.line_index_start]
            line_end = lines[indices.line_index_end]
            line_index_start = line_start.pre_index or line_start.post_index
            line_index_end = line_end.pre_index or line_end.post_index

            res.append(
                ExtendedMatch(
                    match=match.match,
                    match_type=match.match_type,
                    index_start=indices.index_start,
                    index_end=indices.index_end,
                    line_start=line_index_start,
                    line_end=line_index_end,
                    pre_line_start=line_start.pre_index,
                    post_line_start=line_start.post_index,
                    pre_line_end=line_end.pre_index,
                    post_line_end=line_end.post_index,
                )
            )
        return res
