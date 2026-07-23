from inspector.metrics import parse_nvidia_smi, parse_ping


def test_parse_nvidia_smi_basic():
    output = "0, NVIDIA T4, 60.0, 10.0, 20.0, 1000.0, 16000.0, 35.0, 30.0\n"
    gpus = parse_nvidia_smi(output)
    assert len(gpus) == 1
    assert gpus[0].name == "NVIDIA T4"
    assert gpus[0].temperature_c == 60.0


def test_parse_ping_basic():
    output = "rtt min/avg/max/mdev = 1.2/2.3/3.4/0.5 ms\n0% packet loss"
    net = parse_ping("8.8.8.8", output)
    assert net.target == "8.8.8.8"
    assert net.avg_latency_ms == 2.3
    assert net.packet_loss_pct == 0.0
