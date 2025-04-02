import logging
from typing import Any, List, Optional, Tuple
import torch
from torch import nn
from transformers import ModernBertModel, ModernBertConfig

from medcat.config_rel_cat import ConfigRelCAT
from medcat.utils.relation_extraction.ml_utils import create_dense_layers
from medcat.utils.relation_extraction.models import Base_RelationExtraction



class ModernBertModel_RelationExtraction(Base_RelationExtraction):
    """ ModernBertModel class for RelCAT
    """

    name = "modernbertmodel_relcat"

    log = logging.getLogger(__name__)

    def __init__(self, pretrained_model_name_or_path: str, relcat_config: ConfigRelCAT, model_config: ModernBertConfig):
        """ Class to hold the ModernBERT model + model_config

        Args:
            pretrained_model_name_or_path (str): path to load the model from,
                    this can be a HF model i.e: "bert-base-uncased", if left empty, it is normally assumed that a model is loaded from 'model.dat'
                    using the RelCAT.load() method. So if you are initializing/training a model from scratch be sure to base it on some model.
            relcat_config (ConfigRelCAT): relcat config.
            model_config (ModernBertConfig): HF bert config for model.
        """
        super(ModernBertModel_RelationExtraction, self).__init__()

        self.relcat_config: ConfigRelCAT = relcat_config
        self.model_config: ModernBertConfig = model_config

        self.hf_model: ModernBertModel = ModernBertModel(config=model_config)

        if pretrained_model_name_or_path != "":
            self.hf_model = ModernBertModel.from_pretrained(pretrained_model_name_or_path, config=model_config)

        for param in self.hf_model.parameters():
            if self.relcat_config.model.freeze_layers:
                param.requires_grad = False
            else:
                param.requires_grad = True

        self.drop_out = nn.Dropout(self.relcat_config.model.dropout)

        self.relu = nn.ReLU()

        # dense layers
        self.fc1, self.fc2, self.fc3 = create_dense_layers(self.relcat_config)

        self.log.info("RelCAT ModernBertConfig: " + str(self.model_config))

    def get_annotation_schema_tag(self, sequence_output: torch.Tensor, input_ids: torch.Tensor, special_tag: List) -> torch.Tensor:
        """ Gets to token sequences from the sequence_ouput for the specific token
            tag ids in self.relcat_config.general.annotation_schema_tag_ids.

        Args:
            sequence_output (torch.Tensor): hidden states/embeddings for each token in the input text
            input_ids (torch.Tensor): input token ids
            special_tag (List): special annotation token id pairs

        Returns:
            torch.Tensor: new seq_tags
        """

        idx_start = torch.where(input_ids == special_tag[0]) # returns: row ids, idx of token[0]/star token in row
        idx_end = torch.where(input_ids == special_tag[1]) # returns: row ids, idx of token[1]/end token in row

        seen = [] # List to store seen elements and their indices
        duplicate_indices = []

        for i in range(len(idx_start[0])):
            if idx_start[0][i] in seen:
                duplicate_indices.append(i)
            else:
                seen.append(idx_start[0][i])

        if len(duplicate_indices) > 0:
            self.log.info("Duplicate entities found, removing them...")
            for idx_remove in duplicate_indices:
                idx_start_0 = torch.cat((idx_start[0][:idx_remove], idx_start[0][idx_remove + 1:]))
                idx_start_1 = torch.cat((idx_start[1][:idx_remove], idx_start[1][idx_remove + 1:]))
                idx_start = (idx_start_0, idx_start_1) # type: ignore

        seen = []
        duplicate_indices = []

        for i in range(len(idx_end[0])):
            if idx_end[0][i] in seen:
                duplicate_indices.append(i)
            else:
                seen.append(idx_end[0][i])

        if len(duplicate_indices) > 0:
            self.log.info("Duplicate entities found, removing them...")
            for idx_remove in duplicate_indices:
                idx_end_0 = torch.cat((idx_end[0][:idx_remove], idx_end[0][idx_remove + 1:]))
                idx_end_1 = torch.cat((idx_end[1][:idx_remove], idx_end[1][idx_remove + 1:]))
                idx_end = (idx_end_0, idx_end_1) # type: ignore

        assert len(idx_start[0]) == input_ids.shape[0]
        assert len(idx_start[0]) == len(idx_end[0])
        sequence_output_entities = []

        for i in range(len(idx_start[0])):
            to_append = sequence_output[i, idx_start[1][i] + 1:idx_end[1][i], ]

            # to_append = torch.sum(to_append, dim=0)
            to_append, _ = torch.max(to_append, axis=0) # type: ignore

            sequence_output_entities.append(to_append)
        sequence_output_entities = torch.stack(sequence_output_entities)

        return sequence_output_entities

    def output2logits(self, sequence_output: torch.Tensor, input_ids: torch.Tensor, e1_e2_start: torch.Tensor) -> torch.Tensor:
        """

        Args:
            sequence_output (torch.Tensor): hidden states/embeddings for each token in the input text
            input_ids (torch.Tensor): input token ids.
            e1_e2_start (torch.Tensor): annotation tags token position

        Returns:
            torch.Tensor: classification probabilities for each token.
        """

        new_pooled_output: torch.Tensor = torch.empty((1, 1))

        if self.relcat_config.general.annotation_schema_tag_ids:
            annotation_schema_tag_ids_ = [self.relcat_config.general.annotation_schema_tag_ids[i:i + 2] for i in
                                        range(0, len(self.relcat_config.general.annotation_schema_tag_ids), 2)]
            seq_tags = []

            # for each pair of tags (e1,s1) and (e2,s2)
            for each_tags in annotation_schema_tag_ids_:
                seq_tags.append(self.get_annotation_schema_tag(
                    sequence_output, input_ids, each_tags))

            seq_tags = torch.stack(seq_tags, dim=0)

            new_pooled_output = torch.cat((seq_tags[0], seq_tags[1]), dim=1)  # no pooling
        else:
            e1e2_output = []
            temp_e1 = []
            temp_e2 = []

            for i, seq in enumerate(sequence_output):
                # e1e2 token sequences
                temp_e1.append(seq[e1_e2_start[i][0]])
                temp_e2.append(seq[e1_e2_start[i][1]])

            e1e2_output.append(torch.stack(temp_e1, dim=0))
            e1e2_output.append(torch.stack(temp_e2, dim=0))

            #new_pooled_output = torch.cat((pooled_output, *e1e2_output), dim=1)

            del e1e2_output
            del temp_e2
            del temp_e1

        x = self.drop_out(new_pooled_output)
        x = self.fc1(x)
        x = self.drop_out(x)
        x = self.fc2(x)
        classification_logits = self.fc3(x)
        return classification_logits.to(self.relcat_config.general.device)

    def forward(self,
                input_ids: Optional[torch.Tensor] = None,
                attention_mask: Optional[torch.Tensor] = None,
                token_type_ids: Optional[torch.Tensor] = None,
                position_ids: Any = None,
                head_mask: Any = None,
                encoder_hidden_states: Any = None,
                encoder_attention_mask: Any = None,
                Q: Any = None,
                output_attentions: Optional[bool] = None,
                output_hidden_states: Optional[bool] = None,
                inputs_embeds: Optional[torch.FloatTensor] = None,
                e1_e2_start: Any = None,
                return_dict: Optional[bool] = None,
                pooled_output: Any = None) -> Tuple[torch.Tensor, torch.Tensor]:

        if input_ids is not None:
            input_shape = input_ids.size()
        else:
            raise ValueError("You have to specify input_ids")

        if attention_mask is None:
            attention_mask = torch.ones(
                input_shape, device=self.relcat_config.general.device)
        if encoder_attention_mask is None:
            encoder_attention_mask = torch.ones(
                input_shape, device=self.relcat_config.general.device)
        if token_type_ids is None:
            token_type_ids = torch.zeros(
                input_shape, dtype=torch.long, device=self.relcat_config.general.device)

        input_ids = input_ids.to(self.relcat_config.general.device)
        attention_mask = attention_mask.to(self.relcat_config.general.device)

        self.hf_model = self.hf_model.to(self.relcat_config.general.device)
        model_output = self.hf_model(input_ids=input_ids, attention_mask=attention_mask)

        # (batch_size, sequence_length, hidden_size)
        sequence_output = model_output.last_hidden_state

        classification_logits = self.output2logits(
            sequence_output, input_ids, e1_e2_start)

        return model_output, classification_logits.to(self.relcat_config.general.device)
