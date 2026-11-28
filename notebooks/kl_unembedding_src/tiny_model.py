"""Winziges Modell derselben Architektur mit dem echten Vokabular (248320) für den Notebook-Test."""
import torch
from transformers import Qwen3_5MoeConfig, Qwen3_5MoeForConditionalGeneration
from transformers.models.qwen3_5_moe.configuration_qwen3_5_moe import Qwen3_5MoeTextConfig, Qwen3_5MoeVisionConfig

def build():
    torch.manual_seed(0)
    torch.set_num_threads(4)
    tc = Qwen3_5MoeTextConfig(
        vocab_size=248320, hidden_size=64, num_hidden_layers=4,
        layer_types=["linear_attention", "linear_attention", "linear_attention", "full_attention"],
        num_attention_heads=4, num_key_value_heads=2, head_dim=64,
        linear_num_key_heads=2, linear_num_value_heads=4, linear_key_head_dim=16, linear_value_head_dim=16,
        linear_conv_kernel_dim=4, num_experts=8, num_experts_per_tok=2, moe_intermediate_size=16,
        shared_expert_intermediate_size=16,
        rope_parameters={"rope_type": "default", "rope_theta": 10000.0, "partial_rotary_factor": 0.25,
                         "mrope_section": [3, 3, 2], "mrope_interleaved": True})
    vc = Qwen3_5MoeVisionConfig(depth=1, hidden_size=32, intermediate_size=64, num_heads=2, out_hidden_size=64)
    model = Qwen3_5MoeForConditionalGeneration(Qwen3_5MoeConfig(text_config=tc, vision_config=vc)).eval().float()
    with torch.no_grad():
        for name, p in model.named_parameters():
            if p.dim() >= 2:
                p.normal_(std=0.1)
    return model
