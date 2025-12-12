cd /workspace
cd build
cmake ..
make -j
make -j install
cd ../install

# 5 arguments: 
# design option (design name or all), 
# mode (input/output), 
# design name, 
# method (for output, like yuxuan, zyw, zyw_dac), 
# variant (default, inflated),
# terminal size & spacing should be the same (for input) e.g., 80 for 0.8um

design_opt=$1
in_or_out=$2
method=$3
input_variant=$4
terminal_size=$5

if [[ "$design_opt" == "iccad_2023_all" ]]; then
  designs=(swerv_wrapper bp bp_be bp_fe bp_multi ariane133 ariane136 bp_quad)
elif [[ "$design_opt" == "iccad_2022_all" ]]; then 
  designs=(aes ibex)
else
  designs=("$design_opt")
fi


for design_name in "${designs[@]}"; do

    # 6 arguments: 
    # script name, 
    # mode (input/output), 
    # design name, 
    # method (for output, like yuxuan, zyw, zyw_dac), 
    # variant (default, inflated),
    # terminal size & spacing should be the same (for input) e.g., 80 for 0.8um

    python3 dreamplace/convert_file.py "test/convert_file_${input_variant}/${design_name}.json" ${in_or_out} "${design_name}_3D" "${method}" "${input_variant}" "${terminal_size}"

done