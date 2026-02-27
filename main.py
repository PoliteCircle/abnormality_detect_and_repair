from __future__ import annotations

from typing import Set, Tuple, Dict
from pathlib import Path

from bpmn_subprocess import compute_all_process_patterns, normalize_token
from log_check import find_violating_processes

from minimal_abnormal_structure import (
    compute_min_as,
    minas_to_strings,
    simplify_tree_str,
    filter_trace_to_messages,  # 若你 minimal_abnormal_structure.py 里已定义
)
from repair_suggestion import suggest_repairs_for_process, expr_to_str as expr_str2
from experiment_loader import load_experiment
import time

Trace = Tuple[str, ...]


def main():
    # =========================================================
    # 读取实验数据
    # =========================================================
    base = Path(__file__).resolve().parent
    bpmn_path, global_log = load_experiment(base)

    # ================= 开始计时 =================
    start_time = time.perf_counter()

    # 1) 计算每个 participant/process 的消息模式
    infos = compute_all_process_patterns(
        str(bpmn_path),
        tmp_dir="_tmp_process_bpmns",
        interleave_limit=2000,
        debug=True
    )

    # 2) 校验全局日志
    violations = find_violating_processes(
        infos,
        global_log,
        ignore_empty_projection=False,
        debug=True
    )

    # 3) 输出每个异常流程的“新版本流程树 + 详细预分割 + MinAS”
    print("\n" + "=" * 80)
    print(f"[RESULT] violating_processes={len(violations)}")

    # 建索引：process_id -> SubprocessInfo
    # 先建索引：process_id -> info
    # 先建索引：process_id -> info
    info_by_pid = {s.process_id: s for s in infos}

    for v in violations:
        print("\n" + "#" * 80)
        print(f"[VIOL] participant='{v.participant_name}' process_id='{v.process_id}'")

        if v.process_id not in info_by_pid:
            print(f"[VIOL][WARN] cannot find process_id='{v.process_id}' in infos, skip")
            continue

        info = info_by_pid[v.process_id]

        # 子流程树（叶子已被你 rename 成消息名/活动名混合）
        process_tree = info.ast
        print(f"[TREE] raw_tree = {process_tree}")

        # 子流程消息集合：用 MEo ∪ MEi 最稳
        proc_msgs = set(info.MEo) | set(info.MEi)
        print(f"[MSGSET] proc_msgs={sorted(proc_msgs)}")

        # 子流程上的正常/异常投影日志（你要求区分）
        def project_to_process(tr):
            return tuple(x for x in tr if x in proc_msgs)

        normal_proj = set()
        abnormal_proj = set()

        for gt in global_log:
            pt = project_to_process(gt)
            if pt in info.pattern:
                normal_proj.add(pt)
            else:
                abnormal_proj.add(pt)

        print(f"[LOG] normal_proj size={len(normal_proj)} sample={sorted(normal_proj)[:20]}")
        print(f"[LOG] abnormal_proj size={len(abnormal_proj)} sample={sorted(abnormal_proj)[:20]}")

        # 逐条“异常投影”算 MinAS，并打印详细预分割过程
        minas_subtrees_all = []
        for gt, pt in v.bad_global_traces:
            print("-" * 80)
            print(f"[MINAS] global={gt} projected_abnormal={pt}")

            minas = compute_min_as(
                expr_root=process_tree,
                abnormal_tr_raw=pt,
                msg_set=proc_msgs,
                debug=True,
                simplify_debug=True
            )

            print(f"[MINAS] minas_count={len(minas)} minas={minas_to_strings(minas)}")
            minas_subtrees_all.extend(list(minas))

        # 去重汇总（按字符串去重）
        minas_unique = []
        seen = set()
        for st in minas_subtrees_all:
            s = str(st)
            if s not in seen:
                seen.add(s)
                minas_unique.append(st)

        print(f"\n[MINAS SUMMARY for {v.participant_name}] unique_count={len(minas_unique)}")
        for st in minas_unique:
            print(f"  - {st}")

        # 修复建议（你后面要的 repair_suggestion.py）
        repairs = suggest_repairs_for_process(
            process_message_tree=process_tree,  # 如果你想用“简化后的树”，见下面第2点建议
            minas_subtrees=minas_unique,
            normal_traces_proj=normal_proj,
            abnormal_traces_proj=abnormal_proj,
            debug=True
        )

        print("\n" + "=" * 80)
        print(f"[REPAIR SUMMARY] process_id='{v.process_id}' participant='{v.participant_name}'")
        for k, sug_list in repairs.items():
            print("-" * 80)
            print(f"[REPAIR] MinAS={k}")
            if not sug_list:
                print("  (no candidate repairs)")
                continue
            for s in sug_list[:10]:
                print(f"  -> after={s.after_subtree}")

                # 统计：修复前/后分别通过多少条
                print(
                    f"     NORMAL: before {s.before_pass_normals}/{s.before_total_normals}"
                    f" -> after {s.after_pass_normals}/{s.after_total_normals}"
                )
                print(
                    f"     ABNOR : before {s.before_pass_abnormals}/{s.before_total_abnormals}"
                    f" -> after {s.after_pass_abnormals}/{s.after_total_abnormals}"
                )

                # 是否覆盖全部（保留原来的 ok 输出也行）
                print(f"     ok_on_normals={s.ok_on_normals} ok_on_abnormals={s.ok_on_abnormals}")

                print(f"     reason={s.reason}")

    # ================= 结束计时 =================
    end_time = time.perf_counter()
    elapsed = end_time - start_time

    print("\n" + "=" * 80)
    print(f"[TIME] total runtime: {elapsed:.3f} seconds ({elapsed / 60:.2f} min)")


if __name__ == "__main__":
    main()