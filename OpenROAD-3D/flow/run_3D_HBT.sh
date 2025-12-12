#!/bin/bash

# 定义一个函数：接受 任务名、输入文件、日志文件 作为参数
# 注意：根据你下面的调用方式，这里实际上只有3个参数
run_task() {
    local design_fullname=$1
    local design_shortname=$2
    local variant=$3
    local method=$4

    local runtime_log="logs/nangate45_3D/${design_shortname}/HBT/runtime.log"

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting task: $task_name" >> "$runtime_log"
    
    # 记录开始时间戳
    local start_time=$(date +%s)

    # 执行命令
    make DESIGN_CONFIG=designs/nangate45_3D/${design_fullname}/config_HBT.mk do-hbtflow

    local hotspot_time=$(date +%s)

    make DESIGN_CONFIG=designs/nangate45_3D/${design_fullname}/config_HBT.mk do-hotspot
    

    # 记录结束时间戳
    local end_time=$(date +%s)
    
    # 计算耗时
    local open3d_rt=$((hotspot_time - start_time))
    local hotspot_rt=$((end_time - hotspot_time))

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Finished task: $task_name. Open3D Runtime: ${open3d_rt}s, HotSpot Runtime: ${hotspot_rt}s" >> "$runtime_log"
    echo "-----------------------------------------------------" >> "$runtime_log"

    rm -r logs/nangate45_3D/${design_shortname}/${METHOD}_${variant}
    mv logs/nangate45_3D/${design_shortname}/HBT logs/nangate45_3D/${design_shortname}/${METHOD}_${variant}
    mv results/nangate45_3D/${design_shortname}/HBT/*.png logs/nangate45_3D/${design_shortname}/${METHOD}_${variant}/
    mv results/nangate45_3D/${design_shortname}/HBT/hotspot_outputs logs/nangate45_3D/${design_shortname}/${METHOD}_${variant}/
}

export DEF_VARIANT=$1
export METHOD=$2
export OPENROAD_EXE=$(command -v openroad)


# 依次调用函数
run_task "bp_be_top"       "bp_be"           "${DEF_VARIANT}"  "${METHOD}"
run_task "bp_fe_top"       "bp_fe"           "${DEF_VARIANT}"  "${METHOD}"
run_task "bp_multi_top"    "bp_multi"        "${DEF_VARIANT}"  "${METHOD}"
run_task "black_parrot"    "bp"              "${DEF_VARIANT}"  "${METHOD}"
run_task "swerv_wrapper"   "swerv_wrapper"   "${DEF_VARIANT}"  "${METHOD}"
run_task "ariane133"       "ariane133"       "${DEF_VARIANT}"  "${METHOD}"
run_task "ariane136"       "ariane136"       "${DEF_VARIANT}"  "${METHOD}"
# run_task "bp_quad"         "bp_quad"         "${DEF_VARIANT}"  "${METHOD}"