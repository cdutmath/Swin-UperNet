# encoding = utf-8

# @Author     ：Lecheng Wang
# @Time       : ${2025/6/16} ${03:06}
# @Function   : RefineNet
# @Description: Realization of RefineNet architecture

import torch
import torch.nn            as nn
import torch.nn.functional as F
import numpy               as np


def batchnorm(in_planes):
    return nn.BatchNorm2d(in_planes, affine=True, eps=1e-5, momentum=0.1)

def conv3x3(in_planes, out_planes, stride=1, bias=False):
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride, padding=1, bias=bias)

def conv1x1(in_planes, out_planes, stride=1, bias=False):
    return nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, padding=0, bias=bias)

def convbnrelu(in_planes, out_planes, kernel_size, stride=1, groups=1, act=True):
    if act:
        return nn.Sequential(nn.Conv2d(in_planes, out_planes, kernel_size, stride=stride, padding=int(kernel_size/2.), groups=groups, bias=False),
                             nn.BatchNorm2d(out_planes, affine=True, eps=1e-5, momentum=0.1),
                             nn.ReLU6(inplace=True)
                             )
    else:
        return nn.Sequential(nn.Conv2d(in_planes, out_planes, kernel_size, stride=stride, padding=int(kernel_size/2.), groups=groups, bias=False),
                             nn.BatchNorm2d(out_planes, affine=True, eps=1e-5, momentum=0.1)
                             )

class CRPBlock(nn.Module):
    def __init__(self, in_planes, out_planes, n_stages):
        super(CRPBlock, self).__init__()
        for i in range(n_stages):
            setattr(self, '{}_{}'.format(i+1, 'outvar_dimred'), conv3x3(in_planes if (i==0) else out_planes, out_planes, stride=1, bias=False))
        self.stride   = 1
        self.n_stages = n_stages
        self.maxpool  = nn.MaxPool2d(kernel_size=5, stride=1, padding=2)

    def forward(self, x):
        top = x
        for i in range(self.n_stages):
            top = self.maxpool(top)
            top = getattr(self, '{}_{}'.format(i + 1, 'outvar_dimred'))(top)
            x   = top + x
        return x
    
stages_suffixes = {0 : '_conv', 1 : '_conv_relu_varout_dimred'}

class RCUBlock(nn.Module):
    def __init__(self, in_planes, out_planes, n_blocks, n_stages):
        super(RCUBlock, self).__init__()
        for i in range(n_blocks):
            for j in range(n_stages):
                setattr(self, '{}{}'.format(i + 1, stages_suffixes[j]), conv3x3(in_planes if (i == 0) and (j == 0) else out_planes, out_planes, stride=1, bias=(j == 0)))
        self.stride   = 1
        self.n_blocks = n_blocks
        self.n_stages = n_stages
    
    def forward(self, x):
        for i in range(self.n_blocks):
            residual = x
            for j in range(self.n_stages):
                x = F.relu(x)
                x = getattr(self, '{}{}'.format(i + 1, stages_suffixes[j]))(x)
            x += residual
        return x

class BasicBlock(nn.Module):
    expansion = 1
    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1      = conv3x3(inplanes, planes, stride)
        self.bn1        = nn.BatchNorm2d(planes)
        self.relu       = nn.ReLU(inplace=True)
        self.conv2      = conv3x3(planes, planes)
        self.bn2        = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride     = stride

    def forward(self, x):
        residual = x
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class Bottleneck(nn.Module):
    expansion = 4
    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(Bottleneck, self).__init__()
        self.conv1      = nn.Conv2d(inplanes, planes, kernel_size=1, bias=False)
        self.bn1        = nn.BatchNorm2d(planes)
        self.conv2      = nn.Conv2d(planes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn2        = nn.BatchNorm2d(planes)
        self.conv3      = nn.Conv2d(planes, planes * 4, kernel_size=1, bias=False)
        self.bn3        = nn.BatchNorm2d(planes * 4)
        self.relu       = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride     = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class RefineNet(nn.Module):
    def __init__(self, block, layers, in_channels=3, num_classes=21):
        super(RefineNet, self).__init__()
        self.inplanes = 64
        self.do       = nn.Dropout(p=0.5)
        self.conv1    = nn.Conv2d(in_channels, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.bn1      = nn.BatchNorm2d(64)
        self.relu     = nn.ReLU(inplace=True)
        self.maxpool  = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1   = self._make_layer(block, 64,  layers[0])
        self.layer2   = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3   = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4   = self._make_layer(block, 512, layers[3], stride=2)
        self.p_ims1d2_outl1_dimred                = conv3x3(2048, 512, bias=False)
        self.adapt_stage1_b                       = self._make_rcu(512, 512, 2, 2)
        self.mflow_conv_g1_pool                   = self._make_crp(512, 512, 4)
        self.mflow_conv_g1_b                      = self._make_rcu(512, 512, 3, 2)
        self.mflow_conv_g1_b3_joint_varout_dimred = conv3x3(512, 256, bias=False)
        self.p_ims1d2_outl2_dimred                = conv3x3(1024, 256, bias=False)
        self.adapt_stage2_b                       = self._make_rcu(256, 256, 2, 2)
        self.adapt_stage2_b2_joint_varout_dimred  = conv3x3(256, 256, bias=False)
        self.mflow_conv_g2_pool                   = self._make_crp(256, 256, 4)
        self.mflow_conv_g2_b                      = self._make_rcu(256, 256, 3, 2)
        self.mflow_conv_g2_b3_joint_varout_dimred = conv3x3(256, 256, bias=False)

        self.p_ims1d2_outl3_dimred                = conv3x3(512, 256, bias=False)
        self.adapt_stage3_b                       = self._make_rcu(256, 256, 2, 2)
        self.adapt_stage3_b2_joint_varout_dimred  = conv3x3(256, 256, bias=False)
        self.mflow_conv_g3_pool                   = self._make_crp(256, 256, 4)
        self.mflow_conv_g3_b                      = self._make_rcu(256, 256, 3, 2)
        self.mflow_conv_g3_b3_joint_varout_dimred = conv3x3(256, 256, bias=False)

        self.p_ims1d2_outl4_dimred               = conv3x3(256, 256, bias=False)
        self.adapt_stage4_b                      = self._make_rcu(256, 256, 2, 2)
        self.adapt_stage4_b2_joint_varout_dimred = conv3x3(256, 256, bias=False)
        self.mflow_conv_g4_pool                  = self._make_crp(256, 256, 4)
        self.mflow_conv_g4_b                     = self._make_rcu(256, 256, 3, 2)

        self.clf_conv                            = nn.Conv2d(256, num_classes, kernel_size=3, stride=1, padding=1, bias=True)
        self.final_upsample                      = nn.Upsample(scale_factor=4, mode='bilinear', align_corners=True)

    def _make_crp(self, in_planes, out_planes, stages):
        layers = [CRPBlock(in_planes, out_planes,stages)]
        return nn.Sequential(*layers)
    
    def _make_rcu(self, in_planes, out_planes, blocks, stages):
        layers = [RCUBlock(in_planes, out_planes, blocks, stages)]
        return nn.Sequential(*layers)

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        l1 = self.layer1(x)
        l2 = self.layer2(l1)
        l3 = self.layer3(l2)
        l4 = self.layer4(l3)

        l4 = self.do(l4)
        l3 = self.do(l3)

        x4 = self.p_ims1d2_outl1_dimred(l4)
        x4 = self.adapt_stage1_b(x4)
        x4 = self.relu(x4)
        x4 = self.mflow_conv_g1_pool(x4)
        x4 = self.mflow_conv_g1_b(x4)
        x4 = self.mflow_conv_g1_b3_joint_varout_dimred(x4)
        x4 = nn.Upsample(size=l3.size()[2:], mode='bilinear', align_corners=True)(x4)

        x3 = self.p_ims1d2_outl2_dimred(l3)
        x3 = self.adapt_stage2_b(x3)
        x3 = self.adapt_stage2_b2_joint_varout_dimred(x3)
        x3 = x3 + x4
        x3 = F.relu(x3)
        x3 = self.mflow_conv_g2_pool(x3)
        x3 = self.mflow_conv_g2_b(x3)
        x3 = self.mflow_conv_g2_b3_joint_varout_dimred(x3)
        x3 = nn.Upsample(size=l2.size()[2:], mode='bilinear', align_corners=True)(x3)

        x2 = self.p_ims1d2_outl3_dimred(l2)
        x2 = self.adapt_stage3_b(x2)
        x2 = self.adapt_stage3_b2_joint_varout_dimred(x2)
        x2 = x2 + x3
        x2 = F.relu(x2)
        x2 = self.mflow_conv_g3_pool(x2)
        x2 = self.mflow_conv_g3_b(x2)
        x2 = self.mflow_conv_g3_b3_joint_varout_dimred(x2)
        x2 = nn.Upsample(size=l1.size()[2:], mode='bilinear', align_corners=True)(x2)

        x1 = self.p_ims1d2_outl4_dimred(l1)
        x1 = self.adapt_stage4_b(x1)
        x1 = self.adapt_stage4_b2_joint_varout_dimred(x1)
        x1 = x1 + x2
        x1 = F.relu(x1)
        x1 = self.mflow_conv_g4_pool(x1)
        x1 = self.mflow_conv_g4_b(x1)
        x1 = self.do(x1)

        out = self.clf_conv(x1)
        out = self.final_upsample(out)
        return out


#def rf18(num_classes=21, in_channels=3, **kwargs):
#    model = RefineNet(BasicBlock, [2, 2, 2, 2], in_channels=in_channels, num_classes=num_classes, **kwargs)
#    return model
#
#def rf34(num_classes=21, in_channels=3, **kwargs):
#    model = RefineNet(BasicBlock, [3, 4, 6, 3], in_channels=in_channels, num_classes=num_classes, **kwargs)
#    return model

def rf50(num_classes=21, bands=3, **kwargs):
    model = RefineNet(Bottleneck, [3, 4, 6,  3], in_channels=bands, num_classes=num_classes, **kwargs)
    return model

def rf101(num_classes=21, in_channels=3, **kwargs):
    model = RefineNet(Bottleneck, [3, 4, 23, 3], in_channels=bands, num_classes=num_classes, **kwargs)
    return model

def rf152(num_classes=21, in_channels=3, **kwargs):
    model = RefineNet(Bottleneck, [3, 8, 36, 3], in_channels=bands, num_classes=num_classes, **kwargs)
    return model

if __name__ == "__main__":
    from torchinfo  import summary
    from thop       import profile
    device          = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model           = rf50(bands=6, num_classes=3).to(device)
    x               = torch.randn(1, 6, 256, 256).to(device)
    output          = model(x)
    flops, params   = profile(model, inputs=(x, ), verbose=False)

    print('GFLOPs: ', (flops/1e9)/x.shape[0], 'Params(M): ', params/1e6)
    print("Input  shape:", list(x.shape))
    print("Output shape:", list(output.shape))
    summary(model, input_size=(1, 6, 256, 256), device=device.type)