#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import numpy as np
import re
from pathlib import Path
from collections import defaultdict

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import dreamplace.configure as configure
import Params
import PlaceDB
import pdb

################################################################################
# util
################################################################################
def group_similar_lib(name2lib):
    groups = defaultdict(list)
    for k, v in name2lib.items():
        groups[v].append(k)
    new_name2lib = {}
    libs = []
    for idx, (lib, keys) in enumerate(groups.items(), 1):
        for k in keys:
            new_name2lib[k] = idx
        libs.append(lib)
    return libs, new_name2lib


################################################################################
# main
################################################################################
def main():

    params = Params.Params()
    params.load(sys.argv[1])
    placedb = PlaceDB.PlaceDB()
    placedb(params)
    mode = sys.argv[2].lower()
    
    design_name = sys.argv[3]
    method = sys.argv[4]
    input_variant = sys.argv[5]  # for input mode
    terminal_size = int(sys.argv[6]) # for input mode
    
    if mode == "input":
        write_input(placedb, terminal_size, f"../data/converted_input/{input_variant}/{design_name}.input")

    elif mode == "output":
        convert_output(placedb, params, design_name, method, input_variant)

    else:
        raise RuntimeError("2nd argv must be `input` or `output`")


################################################################################
# ----------  mode == "output"  ------------------------------------------------
################################################################################
def convert_output(placedb, params, design_name, method, input_variant):
    txt_file = Path(f"../data/binary_output/{input_variant}/{method}/{design_name}.txt")

    target_dir = Path(f"../data/converted_output/{input_variant}/{method}")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
    
    name2die = {}
    name2orient = {}
    orient_map = {"R0": "N", "R90": "E", "R180": "S", "R270": "W", "R360": "N"}

    id2name = {i: n.decode("utf-8") for i, n in enumerate(placedb.node_names)}

    ThreeDNets = []          # 存 net_id (int, 0-based)
    HBT_coords = []          # [(x,y), ...]  scale 后坐标
    HBT_types = []           # 0: BOT-in, 1: TOP-in
    Splited_nets = []        # [(bottom_pin_list, top_pin_list), ...]

    ############################################################################
    # 1. 读取 true3d 布局文本，收集 HBT 与 3D-net 拆分信息
    ############################################################################
    with txt_file.open() as f:
        die_name = None
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("TopDie"):
                die_name = "_upper"
            elif line.startswith("BottomDie"):
                die_name = "_bottom"

            elif line.startswith("Inst"):
                _, inst_id, x, y, ori = line.split()
                if inst_id[0] != "C":
                    continue
                node_id = int(inst_id[1:]) - 1
                node_name = id2name[node_id]
                name2die[node_name] = die_name
                placedb.node_x[node_id] = int(x) * 20 * placedb.scale_factor
                placedb.node_y[node_id] = int(y) * 20 * placedb.scale_factor
                placedb.node_orient[node_id] = orient_map[ori]
                name2orient[node_name] = orient_map[ori]

            elif line.startswith("Terminal"):
                # 格式：Terminal N123 100 200
                _, net, x_co, y_co = line.split()
                net_id = int(net[1:]) - 1 # keep 0-based id later
                net_name = placedb.net_names[net_id].decode("utf-8")
                if net_name in {
                    'clk_i', 'rst_n_i', 'clk',
                    'p_bsg_tag_clk_i', 'p_clk_A_i', 'p_clk_B_i', 'p_clk_C_i',
                    'p_ci_clk_i', 'p_ci2_tkn_i', 'p_co_clk_i', 'p_co2_tkn_i'
                }:
                    print(f"[WARNING] Skip net {placedb.net_names[net_id]}")
                    continue
                x_co, y_co = int(x_co), int(y_co)
                x_real = x_co * 20 + params.shift_factor[0]
                y_real = y_co * 20 + params.shift_factor[1]

                ThreeDNets.append(net_id)
                HBT_coords.append((x_real, y_real))

                connected_pins = placedb.net2pin_map[net_id]
                bottom_pins, top_pins = [], []
                for pin_id in connected_pins:
                    node_id = placedb.pin2node_map[pin_id]
                    node_name = id2name[node_id]
                    if node_name not in name2die:
                        die = "_bottom"  # IO pin 在 bottom   
                    else:
                        die = name2die[node_name]
                    pin_name = placedb.pin_names[pin_id].decode("utf-8")
                    direction = placedb.pin_direct[pin_id].decode("utf-8")  # 修改字段名
                    if die == "_bottom":
                        bottom_pins.append((node_name, pin_name))
                        if direction == "OUTPUT":
                            HBT_types.append(0)
                    elif die == "_upper":
                        top_pins.append((node_name, pin_name))
                        if direction == "OUTPUT":
                            HBT_types.append(1)
                    else:
                        raise RuntimeError("die_name not set")
                Splited_nets.append((bottom_pins, top_pins))

    ############################################################################
    # 2. 写一次原 DEF，再在内存里重写 Components / Nets
    ############################################################################
    def_file = target_dir / f"{design_name}.def"
    def_file.parent.mkdir(parents=True, exist_ok=True)
    
    placedb.write(params, str(def_file))  # 原生 DreamPlace DEF

    new_def_lines = []
    skip_next_flag = False  # 用于跳过 TAPCELL 后一行
    with def_file.open() as f:
        inside_comp = False
        inside_nets = False
        process_fakeram_flag = False
        comp_buf = []        # 用于 components end 前插入 HBT
        net_buf = []         # 当前正在收集的一条网
        threeD_map = {n: i for i, n in enumerate(ThreeDNets)}  # net_id -> idx

        for raw in f:
            line = raw.rstrip("\n")

            # ---------------- Components ----------------
            if line.strip().startswith("COMPONENTS"):
                inside_comp = True
            elif inside_comp and line.strip().startswith("END COMPONENTS"):
                inside_comp = False
                # 1) 把缓存的 components 行写出
                new_def_lines.extend(comp_buf)
                comp_buf.clear()
                # 2) 插入 HBT 组件
                for idx, net_id in enumerate(ThreeDNets):
                    x, y = HBT_coords[idx]
                    if HBT_types[idx] == 0:
                        inst_name = f"HBT_BOTIN_{net_id}"
                        new_def_lines.append(f"  - {inst_name} HBT_BOTIN")
                    else:
                        inst_name = f"HBT_TOPIN_{net_id}"
                        new_def_lines.append(f"  - {inst_name} HBT_TOPIN")
                    
                    new_def_lines.append(f"    + PLACED ( {int(x)} {int(y)} ) N ;")
                # 3) 写 END COMPONENTS 行自身
                new_def_lines.append(line)
                continue

            if inside_comp:
                if 'fakeram' in line:
                    process_fakeram_flag = True
                    node_name, lef_name = line.split()[1:3]
                    line = line.replace(lef_name, lef_name + name2die[node_name])
                    comp_buf.append(line)
                    continue
                if process_fakeram_flag:
                    line = line.replace('PLACED', 'FIXED')
                    # adjust y coordinate for pin alignment
                    new_orient = name2orient[node_name]
                    x = line.split()[3]
                    y = line.split()[4]
                    orig_orient = line.split()[6]
                    # num_row = int((float(y) - 70) / 280)
                    # y_ = str(280 * num_row + 70)
                    # x_ = str(int((float(x)) / 10.0) * 10)
                    # line = line.replace(y, y_)
                    # line = line.replace(x, x_)
                    line = line.replace(orig_orient, new_orient)
                    comp_buf.append(line)
                    process_fakeram_flag = False
                    continue
                
                if 'COMPONENTS' in line:
                    comp_buf.append(line)
                    continue

                if skip_next_flag:
                    skip_next_flag = False
                    continue

                if 'TAPCELL' in line:
                    skip_next_flag = True
                    continue

                if '-' in line:
                    line_ls = line.split()
                    node_name, lef_name = line_ls[1], line_ls[2]
                    line = line.replace(lef_name, lef_name + name2die[node_name])
                    comp_buf.append(line)
                    continue

                comp_buf.append(line)
                continue

            # ---------------- NETS ----------------
            if line.strip().startswith("NETS"):
                inside_nets = True
                new_def_lines.append(line)
                continue
            elif inside_nets and line.strip().startswith("END NETS"):
                # flush 最后一条网
                if net_buf:
                    new_def_lines.extend(process_net_buf(placedb, net_buf, Splited_nets,
                                                         threeD_map, HBT_types))
                    net_buf = []
                inside_nets = False
                new_def_lines.append(line)
                continue

            if inside_nets:
                net_buf.append(line)
                # 一条网以 “;” 结束
                if ";" in line:
                    new_def_lines.extend(process_net_buf(placedb, net_buf, Splited_nets,
                                                         threeD_map, HBT_types))
                    net_buf = []
                continue

            # 其他段直接抄
            new_def_lines.append(line)

    with def_file.open("w") as fw:
        fw.write("\n".join(new_def_lines))
    print(f"[INFO] DEF regenerated at {def_file}")


###############################################################################
# ----------  NETS helper ------------------------------------------------------
###############################################################################
import re
NET_START = re.compile(r"^\s*-\s+(\S+)")
PIN_RE    = re.compile(r"\(\s*(\S+)\s+(\S+)\s*\)")

def process_net_buf(placedb, buf, Splited_nets, threeD_map, HBT_types):
    """
    将 DEF 中一条 3-D 网 (buf) 拆成上下两个子网。
    拆分后两个子网都连到同一个 HBT 端口，
    HBT 的具体类型由 HBT_types 决定，而不是名字里带 BOT/TOP。
    """
    first = buf[0]
    m = NET_START.match(first)
    if not m:                 # 解析失败，原样返回
        return buf

    net_name = m.group(1)

    net_id = int(placedb.net_name2id_map[net_name])
    if net_id not in threeD_map:
        return buf            # 普通网，原样返回

    idx = threeD_map[net_id]               # 该 3-D 网在各种列表中的下标
    bot_pins, top_pins = Splited_nets[idx]

    # -------- 组装两条子网 ----------
    def build(subnet_name, pin_list, hbt_inst, IO):
        lines = [f"- {subnet_name}"]
        for inst, pin in pin_list:         # 原有 pin
            if inst == pin:
                lines.append(f"( PIN {pin} )")
            else:
                lines.append(f"( {inst} {pin} )")
        lines.append(f"( {hbt_inst} {IO} )")  # HBT 端口
        lines.append("+ USE SIGNAL ;")     # 简化属性
        return lines
    
    if HBT_types[idx] == 0:
        hbt_inst = (f"HBT_BOTIN_{net_id}")
        new_lines  = build(f"N{net_id}_BOT", bot_pins, hbt_inst, "BOT")
        new_lines += build(f"N{net_id}_TOP", top_pins, hbt_inst, "TOP")
    else:
        hbt_inst = (f"HBT_TOPIN_{net_id}")
        new_lines  = build(f"N{net_id}_BOT", bot_pins, hbt_inst, "BOT")
        new_lines += build(f"N{net_id}_TOP", top_pins, hbt_inst, "TOP")

    return new_lines


################################################################################
# ----------  mode == "input" --------------------------------------------------
# （此部分沿用原逻辑，无关键修改，略）
################################################################################
def write_input(placedb, terminal_size, outfile):
    
    with open(outfile, 'w', encoding='utf-8') as f:
        
        
        # size here = um unit * 100
        placedb.node_size_x = np.round(placedb.node_size_x / placedb.scale_factor / 20).astype(int)
        placedb.node_size_y = np.round(placedb.node_size_y / placedb.scale_factor / 20).astype(int)
        placedb.row_height = np.round(placedb.row_height / placedb.scale_factor / 20).astype(int)
        placedb.pin_offset_x = np.round(placedb.pin_offset_x / placedb.scale_factor / 20).astype(int)
        placedb.pin_offset_y = np.round(placedb.pin_offset_y / placedb.scale_factor / 20).astype(int)
        placedb.xl = np.round(placedb.xl / placedb.scale_factor / 20).astype(int)
        placedb.yl = np.round(placedb.yl / placedb.scale_factor / 20).astype(int)
        placedb.xh = np.round(placedb.xh / placedb.scale_factor / 20).astype(int)
        placedb.yh = np.round(placedb.yh / placedb.scale_factor / 20).astype(int)

        mean_node_area, num = 0, 0
        for node_name in placedb.node_names:
            node = placedb.node_name2id_map[node_name.decode('utf-8')]
            if node < (placedb.num_physical_nodes - placedb.num_terminal_NIs):  # exclude IO ports
                node_area = placedb.node_size_x[node] * placedb.node_size_y[node]
                mean_node_area += node_area
                num += 1
        mean_node_area = mean_node_area / num

        # pdb.set_trace()
        
        # arange lib info
        names = []
        id2name = {}
        name2lib = {}
        IO_names = []
        has_macro_flag = False
        for node_name in placedb.node_names:
            
            # node name
            node_name = node_name.decode('utf-8')
            node_id = placedb.node_name2id_map[node_name]
            
            # exclude IO ports
            if node_id >= (placedb.num_physical_nodes - placedb.num_terminal_NIs):  
                IO_names.append(node_name)
                continue
            
            names.append(node_name)
            id2name[node_id] = node_name
            
            # if macro
            is_macro = "Y" if ((placedb.node_size_x[node_id] * placedb.node_size_y[node_id] > (mean_node_area * 10)) and (placedb.node_size_y[node_id] > (placedb.row_height * 5))) else "N"
            if is_macro == "Y":
                has_macro_flag = True
            # pin count
            pins = placedb.node2pin_map[node_id]
            
            lib = []
            lib.extend([is_macro, placedb.node_size_x[node_id], placedb.node_size_y[node_id], len(pins)])
            # add pin info
            for i, pin_id in enumerate(pins):
                lib.extend([(i, placedb.pin_offset_x[pin_id], placedb.pin_offset_y[pin_id])])
            
            name2lib[node_name] = tuple(lib)
            
        libs, name2lib = group_similar_lib(name2lib)
        # write tech
        f.write("NumTechnologies 1 \nTech TA {:d} \n".format(len(libs)))
        # write lib
        for i, lib in enumerate(libs, 1):
            if has_macro_flag:
                f.write("LibCell {} MC{:d} {:d} {:d} {:d} \n".format(lib[0], i, lib[1], lib[2], lib[3]))
            else:
                f.write("LibCell MC{:d} {:d} {:d} {:d} \n".format(i, lib[1], lib[2], lib[3]))
            for pin in lib[4:]:
                f.write('Pin P{:d} {:d} {:d} \n'.format(pin[0] + 1, pin[1], pin[2]))
            
        # write die size
        f.write("\nDieSize {:d} {:d} {:d} {:d} \n\n".format(placedb.xl, placedb.yl, placedb.xh, placedb.yh))
        f.write("TopDieMaxUtil 80 \nBottomDieMaxUtil 80 \n\n")
        
        # write rows
        repeat_count = int(placedb.yh / placedb.row_height)
        f.write("TopDieRows 0 0 {:d} {:d} {:d} \n".format(placedb.xh, placedb.row_height, repeat_count))
        f.write("BottomDieRows 0 0 {:d} {:d} {:d} \n\n".format(placedb.xh, placedb.row_height, repeat_count))
        
        # write tech
        f.write("TopDieTech TA \nBottomDieTech TA \n\n")
        
        # write HBT
        f.write(f"TerminalSize {terminal_size} {terminal_size} \nTerminalSpacing {terminal_size} \nTerminalCost 10 \n\n")
        
        # write instances
        f.write("NumInstances {:d} \n".format(len(names)))
        for name in names:
            node_id = placedb.node_name2id_map[name]
            f.write("Inst C{} MC{} \n".format(node_id + 1, name2lib[name]))
            
        # write nets
        is_IO = False
        net_block = ""
        num_net = 0
        for net_name in placedb.net_names:
            net_name = net_name.decode('utf-8')
            net_id = placedb.net_name2id_map[net_name]
            net_pins = placedb.net2pin_map[net_id]
            num_pins = len(net_pins)
            pin_line = ""
            for pin in net_pins:
                node_id = placedb.pin2node_map[pin]
                if node_id not in id2name.keys():
                    is_IO = True
                    continue
                else:
                    pins = placedb.node2pin_map[node_id]
                    pin_line += "Pin C{}/P{} \n".format(node_id + 1, np.where(pins == pin)[0][0] + 1)
            
            if is_IO:
                is_IO = False
                continue
            if num_pins == 1:
                continue
            net_block += "Net N{} {} \n".format(net_id + 1, num_pins)
            net_block += pin_line
            num_net += 1


        f.write("\nNumNets {:d} \n".format(num_net))
        f.write(net_block)
    


################################################################################
if __name__ == "__main__":
    main()