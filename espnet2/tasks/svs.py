"""Singing-voice-synthesis task."""

import argparse
import logging
from pathlib import Path
from typing import Callable, Collection, Dict, List, Optional, Tuple, Union

import numpy as np
import torch
import yaml
from typeguard import typechecked

from espnet2.gan_svs.joint import JointScore2Wav
from espnet2.gan_svs.vits import VITS
from espnet2.layers.abs_normalize import AbsNormalize
from espnet2.layers.global_mvn import GlobalMVN
from espnet2.svs.abs_svs import AbsSVS
from espnet2.svs.discrete.toksing import TokSing
from espnet2.svs.discrete_svs_espnet_model import ESPnetDiscreteSVSModel
from espnet2.svs.espnet_model import ESPnetSVSModel
from espnet2.svs.feats_extract.score_feats_extract import (
    FrameScoreFeats,
    SyllableScoreFeats,
)
from espnet2.svs.naive_rnn.naive_rnn import NaiveRNN
from espnet2.svs.naive_rnn.naive_rnn_dp import NaiveRNNDP

# TODO(Yuning): Models to be added
from espnet2.svs.singing_tacotron.singing_tacotron import singing_tacotron
from espnet2.svs.xiaoice.XiaoiceSing import XiaoiceSing

# from espnet2.svs.encoder_decoder.transformer.transformer import Transformer
# from espnet2.svs.mlp_singer.mlp_singer import MLPSinger
# from espnet2.svs.glu_transformer.glu_transformer import GLU_Transformer
from espnet2.tasks.abs_task import AbsTask
from espnet2.train.abs_espnet_model import AbsESPnetModel
from espnet2.train.class_choices import ClassChoices
from espnet2.train.collate_fn import CommonCollateFn
from espnet2.train.preprocessor import SVSPreprocessor
from espnet2.train.trainer import Trainer
from espnet2.tts.feats_extract.abs_feats_extract import AbsFeatsExtract
from espnet2.tts.feats_extract.dio import Dio
from espnet2.tts.feats_extract.energy import Energy
from espnet2.tts.feats_extract.linear_spectrogram import LinearSpectrogram
from espnet2.tts.feats_extract.log_mel_fbank import LogMelFbank
from espnet2.tts.feats_extract.log_spectrogram import LogSpectrogram
from espnet2.tts.feats_extract.ying import Ying

# from espnet2.svs.xiaoice.XiaoiceSing import XiaoiceSing_noDP
# from espnet2.svs.bytesing.bytesing import ByteSing
from espnet2.tts.utils import ParallelWaveGANPretrainedVocoder
from espnet2.utils.get_default_kwargs import get_default_kwargs
from espnet2.utils.griffin_lim import Spectrogram2Waveform
from espnet2.utils.nested_dict_action import NestedDictAction
from espnet2.utils.types import int_or_none, str2bool, str_or_none

# TODO(Yuning): Add singing augmentation

feats_extractor_choices = ClassChoices(
    "feats_extract",
    classes=dict(
        fbank=LogMelFbank,
        spectrogram=LogSpectrogram,
        linear_spectrogram=LinearSpectrogram,
    ),
    type_check=AbsFeatsExtract,
    default="fbank",
)

score_feats_extractor_choices = ClassChoices(
    "score_feats_extract",
    classes=dict(
        frame_score_feats=FrameScoreFeats, syllable_score_feats=SyllableScoreFeats
    ),
    type_check=AbsFeatsExtract,
    default="frame_score_feats",
)

pitch_extractor_choices = ClassChoices(
    "pitch_extract",
    classes=dict(dio=Dio),
    type_check=AbsFeatsExtract,
    default=None,
    optional=True,
)
energy_extractor_choices = ClassChoices(
    "energy_extract",
    classes=dict(energy=Energy),
    type_check=AbsFeatsExtract,
    default=None,
    optional=True,
)
normalize_choices = ClassChoices(
    "normalize",
    classes=dict(global_mvn=GlobalMVN),
    type_check=AbsNormalize,
    default="global_mvn",
    optional=True,
)
pitch_normalize_choices = ClassChoices(
    "pitch_normalize",
    classes=dict(global_mvn=GlobalMVN),
    type_check=AbsNormalize,
    default=None,
    optional=True,
)
ying_extractor_choices = ClassChoices(
    "ying_extract",
    classes=dict(ying=Ying),
    type_check=AbsFeatsExtract,
    default=None,
    optional=True,
)
energy_normalize_choices = ClassChoices(
    "energy_normalize",
    classes=dict(global_mvn=GlobalMVN),
    type_check=AbsNormalize,
    default=None,
    optional=True,
)
svs_choices = ClassChoices(
    "svs",
    classes=dict(
        # transformer=Transformer,
        # glu_transformer=GLU_Transformer,
        # bytesing=ByteSing,
        naive_rnn=NaiveRNN,
        naive_rnn_dp=NaiveRNNDP,
        xiaoice=XiaoiceSing,
        toksing=TokSing,
        # xiaoice_noDP=XiaoiceSing_noDP,
        vits=VITS,
        joint_score2wav=JointScore2Wav,
        # mlp=MLPSinger,
        singing_tacotron=singing_tacotron,
    ),
    type_check=AbsSVS,
    default="naive_rnn",
)
model_type_choices = ClassChoices(
    "model_type",
    classes=dict(
        svs=ESPnetSVSModel,
        discrete_svs=ESPnetDiscreteSVSModel,
    ),
    type_check=AbsESPnetModel,
    default="svs",
)


class SVSTask(AbsTask):
    num_optimizers: int = 1

    # Add variable objects configurations
    class_choices_list = [
        # --score_extractor and --score_extractor_conf
        score_feats_extractor_choices,
        # --feats_extractor and --feats_extractor_conf
        feats_extractor_choices,
        # --normalize and --normalize_conf
        normalize_choices,
        # --svs and --svs_conf
        svs_choices,
        # --pitch_extract and --pitch_extract_conf
        pitch_extractor_choices,
        # --pitch_normalize and --pitch_normalize_conf
        pitch_normalize_choices,
        # --ying_extract and --ying_extract_conf
        ying_extractor_choices,
        # --energy_extract and --energy_extract_conf
        energy_extractor_choices,
        # --energy_normalize and --energy_normalize_conf
        energy_normalize_choices,
        # --model_type and --model_type_conf
        model_type_choices,
    ]

    # If you need to modify train() or eval() procedures, change Trainer class here
    trainer = Trainer

    @classmethod
    @typechecked
    def add_task_arguments(cls, parser: argparse.ArgumentParser):
        # NOTE(kamo): Use '_' instead of '-' to avoid confusion
        group = parser.add_argument_group(description="Task related")

        # NOTE(kamo): add_arguments(..., required=True) can't be used
        # to provide --print_config mode. Instead of it, do as
        required = parser.get_default("required")
        required += ["token_list"]

        group.add_argument(
            "--token_list",
            type=str_or_none,
            default=None,
            help="A text mapping int-id to token",
        )
        group.add_argument(
            "--odim",
            type=int_or_none,
            default=None,
            help="The number of dimension of output feature",
        )
        group.add_argument(
            "--model_conf",
            action=NestedDictAction,
            default=get_default_kwargs(ESPnetSVSModel),
            help="The keyword arguments for model class.",
        )

        group = parser.add_argument_group(description="Preprocess related")
        group.add_argument(
            "--use_preprocessor",
            type=str2bool,
            default=True,
            help="Apply preprocessing to data or not",
        )
        group.add_argument(
            "--token_type",
            type=str,
            default="phn",
            choices=["bpe", "char", "word", "phn"],
            help="The text will be tokenized in the specified level token",
        )
        group.add_argument(
            "--bpemodel",
            type=str_or_none,
            default=None,
            help="The model file of sentencepiece",
        )
        parser.add_argument(
            "--non_linguistic_symbols",
            type=str_or_none,
            help="non_linguistic_symbols file path",
        )
        parser.add_argument(
            "--cleaner",
            type=str_or_none,
            choices=[None, "tacotron", "jaconv", "vietnamese"],
            default=None,
            help="Apply text cleaning",
        )
        parser.add_argument(
            "--g2p",
            type=str_or_none,
            choices=[
                None,
                "g2p_en",
                "g2p_en_no_space",
                "pyopenjtalk",
                "pyopenjtalk_kana",
                "pyopenjtalk_accent",
                "pyopenjtalk_accent_with_pause",
                "pypinyin_g2p",
                "pypinyin_g2p_phone",
                "pypinyin_g2p_phone_without_prosody",
                "espeak_ng_arabic",
            ],
            default=None,
            help="Specify g2p method if --token_type=phn",
        )

        parser.add_argument(
            "--fs",
            type=int,
            default=24000,  # BUG: another fs in feats_extract_conf
            help="sample rate",
        )
        parser.add_argument(
            "--discrete_token_layers",
            type=int,
            default=1,
            help="layers of discrete tokens",
        )
        parser.add_argument(
            "--nclusters",
            type=int,
            default=1024,
            help="number of cluster centers",
        )

        for class_choices in cls.class_choices_list:
            # Append --<name> and --<name>_conf.
            # e.g. --encoder and --encoder_conf
            class_choices.add_arguments(group)

    @classmethod
    @typechecked
    def build_collate_fn(cls, args: argparse.Namespace, train: bool) -> Callable[
        [Collection[Tuple[str, Dict[str, np.ndarray]]]],
        Tuple[List[str], Dict[str, torch.Tensor]],
    ]:
        return CommonCollateFn(
            float_pad_value=0.0,
            int_pad_value=0,
            not_sequence=["spembs", "sids", "lids"],
        )

    @classmethod
    @typechecked
    def build_preprocess_fn(
        cls, args: argparse.Namespace, train: bool
    ) -> Optional[Callable[[str, Dict[str, np.array]], Dict[str, np.ndarray]]]:
        if args.use_preprocessor:
            retval = SVSPreprocessor(
                train=train,
                token_type=args.token_type,
                token_list=args.token_list,
                bpemodel=args.bpemodel,
                non_linguistic_symbols=args.non_linguistic_symbols,
                text_cleaner=args.cleaner,
                g2p_type=args.g2p,
                fs=args.fs,
                hop_length=args.feats_extract_conf["hop_length"],
            )
        else:
            retval = None

        return retval

    @classmethod
    def required_data_names(
        cls, train: bool = True, inference: bool = False
    ) -> Tuple[str, ...]:
        if not inference:
            retval = ("text", "singing", "score", "label")
        else:
            # Inference mode
            retval = ("text", "score", "label")
        return retval

    @classmethod
    def optional_data_names(
        cls, train: bool = True, inference: bool = False
    ) -> Tuple[str, ...]:
        if not inference:
            retval = (
                "spembs",
                "durations",
                "pitch",
                "energy",
                "sids",
                "lids",
                "feats",
                "ying",
                "discrete_token",
            )
        else:
            # Inference mode
            retval = (
                "spembs",
                "singing",
                "pitch",
                "durations",
                "sids",
                "lids",
                "discrete_token",
            )
        return retval

    @classmethod
    @typechecked
    def build_model(cls, args: argparse.Namespace) -> ESPnetSVSModel:
        if isinstance(args.token_list, str):
            with open(args.token_list, encoding="utf-8") as f:
                token_list = [line.rstrip() for line in f]

            # "args" is saved as it is in a yaml file by BaseTask.main().
            # Overwriting token_list to keep it as "portable".
            args.token_list = token_list.copy()
        elif isinstance(args.token_list, (tuple, list)):
            token_list = args.token_list.copy()
        else:
            raise RuntimeError("token_list must be str or dict")

        vocab_size = len(token_list)
        logging.info(f"Vocabulary size: {vocab_size }")

        kwargs = dict()

        raw_model_type = getattr(args, "model_type", "svs")
        # 1. feats_extract
        if args.odim is None:
            # Extract features in the model
            feats_extract_class = feats_extractor_choices.get_class(args.feats_extract)
            feats_extract = feats_extract_class(**args.feats_extract_conf)
            odim = feats_extract.output_size()
        else:
            # Give features from data-loader
            args.feats_extract = None
            args.feats_extract_conf = None
            feats_extract = None
            odim = args.odim

        if raw_model_type == "discrete_svs":
            odim = args.nclusters
            discrete_token_layers = args.discrete_token_layers
            kwargs.update(discrete_token_layers=discrete_token_layers)

        # 2. Normalization layer
        if args.normalize is not None:
            normalize_class = normalize_choices.get_class(args.normalize)
            normalize = normalize_class(**args.normalize_conf)
        else:
            normalize = None

        # 3. SVS
        svs_class = svs_choices.get_class(args.svs)
        if raw_model_type == "discrete_svs":
            svs = svs_class(
                idim=vocab_size,
                odim=odim,
                discrete_token_layers=discrete_token_layers,
                **args.svs_conf,
            )
        else:
            svs = svs_class(idim=vocab_size, odim=odim, **args.svs_conf)
        kwargs.update(svs=svs)

        # 4. Extra components
        score_feats_extract = None
        pitch_extract = None
        ying_extract = None
        energy_extract = None
        pitch_normalize = None
        energy_normalize = None
        logging.info(f"args:{args}")
        if getattr(args, "score_feats_extract", None) is not None:
            score_feats_extract_class = score_feats_extractor_choices.get_class(
                args.score_feats_extract
            )
            score_feats_extract = score_feats_extract_class(
                **args.score_feats_extract_conf
            )
        if getattr(args, "pitch_extract", None) is not None:
            pitch_extract_class = pitch_extractor_choices.get_class(args.pitch_extract)
            if args.pitch_extract_conf.get("reduction_factor", None) is not None:
                assert args.pitch_extract_conf.get(
                    "reduction_factor", None
                ) == args.svs_conf.get("reduction_factor", 1)
            else:
                args.pitch_extract_conf["reduction_factor"] = args.svs_conf.get(
                    "reduction_factor", 1
                )
            pitch_extract = pitch_extract_class(**args.pitch_extract_conf)
        if getattr(args, "ying_extract", None) is not None:
            ying_extract_class = ying_extractor_choices.get_class(
                args.ying_extract,
            )

            ying_extract = ying_extract_class(
                **args.ying_extract_conf,
            )
        if getattr(args, "energy_extract", None) is not None:
            if args.energy_extract_conf.get("reduction_factor", None) is not None:
                assert args.energy_extract_conf.get(
                    "reduction_factor", None
                ) == args.svs_conf.get("reduction_factor", 1)
            else:
                args.energy_extract_conf["reduction_factor"] = args.svs_conf.get(
                    "reduction_factor", 1
                )
            energy_extract_class = energy_extractor_choices.get_class(
                args.energy_extract
            )
            energy_extract = energy_extract_class(**args.energy_extract_conf)
        if getattr(args, "pitch_normalize", None) is not None:
            pitch_normalize_class = pitch_normalize_choices.get_class(
                args.pitch_normalize
            )
            pitch_normalize = pitch_normalize_class(**args.pitch_normalize_conf)
        if getattr(args, "energy_normalize", None) is not None:
            energy_normalize_class = energy_normalize_choices.get_class(
                args.energy_normalize
            )
            energy_normalize = energy_normalize_class(**args.energy_normalize_conf)

        kwargs.update(text_extract=score_feats_extract)
        kwargs.update(feats_extract=feats_extract)
        kwargs.update(score_feats_extract=score_feats_extract)
        kwargs.update(label_extract=score_feats_extract)
        kwargs.update(duration_extract=score_feats_extract)
        kwargs.update(pitch_extract=pitch_extract)
        kwargs.update(energy_extract=energy_extract)
        kwargs.update(ying_extract=ying_extract)
        kwargs.update(normalize=normalize)
        kwargs.update(pitch_normalize=pitch_normalize)
        kwargs.update(energy_normalize=energy_normalize)

        # 5. Build model
        model_class = model_type_choices.get_class(raw_model_type)
        model = model_class(
            **kwargs,
            **args.model_conf,
        )
        return model

    @classmethod
    def build_vocoder_from_file(
        cls,
        vocoder_config_file: Union[Path, str] = None,
        vocoder_file: Union[Path, str] = None,
        model: Optional[ESPnetSVSModel] = None,
        device: str = "cpu",
    ):
        logging.info(f"vocoder_config_file: {vocoder_config_file}")
        logging.info(f"vocoder_file: {vocoder_file}")

        # Build vocoder
        if vocoder_file is None:
            # If vocoder file is not provided, use griffin-lim as a vocoder
            vocoder_conf = {}
            if vocoder_config_file is not None:
                vocoder_config_file = Path(vocoder_config_file)
                with vocoder_config_file.open("r", encoding="utf-8") as f:
                    vocoder_conf = yaml.safe_load(f)
            if model.feats_extract is not None:
                vocoder_conf.update(model.feats_extract.get_parameters())
            if (
                "n_fft" in vocoder_conf
                and "n_shift" in vocoder_conf
                and "fs" in vocoder_conf
            ):
                return Spectrogram2Waveform(**vocoder_conf)
            else:
                logging.warning("Vocoder is not available. Skipped its building.")
                return None

        elif str(vocoder_file).endswith(".pkl"):
            # If the extension is ".pkl", the model is trained with parallel_wavegan
            vocoder = ParallelWaveGANPretrainedVocoder(
                vocoder_file, vocoder_config_file
            )
            return vocoder.to(device)

        else:
            raise ValueError(f"{vocoder_file} is not supported format.")
