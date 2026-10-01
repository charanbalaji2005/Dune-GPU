# Host interface (command processor)

64-bit commands enter through a ready/valid port and an 8-entry FIFO
(`tfx_fifo`); 64-bit responses leave through a one-entry elastic buffer
(`tfx_token_channel`) and are held stable under backpressure (assertion in
`tfx_command_processor.sv`).

```
command   [63:60] op   [59:32] arg (28 bits)   [31:0] data
response  [63:60] op echo (0xF = error)   [59:32] arg echo   [31:0] data
```

| Op | Name | arg | data | Response |
|---:|---|---|---|---|
| 0 | NOP | – | – | none |
| 1 | WRITE_IMEM | word address | instruction | none |
| 2 | WRITE_SPM | word address | value | none |
| 3 | READ_SPM | word address | – | value |
| 4 | SET_ARG | index 0-7 | value | none |
| 5 | LAUNCH | – | [3:0] warps, [5:4] policy | when the kernel completes; data = cycles |
| 6 | READ_PERF | counter 0-15 | – | counter value |
| 7 | READ_FAULT | warp | – | {code[19:16], pc[15:0]} (4-bit code since v1.1; codes 1-7 unchanged) |
| 8 | READ_REG | {warp[12:10], lane[9:5], reg[4:0]} | – | register value |
| other | – | – | – | error, data = 0, arg[3:0] = offending op |

Writes and reads to imem/scratchpad/args are ignored while a kernel runs (the
command processor is blocked in the RUN state until completion anyway).
`tools/cosim/cosim.py::build_commands` is a complete reference host driver.
