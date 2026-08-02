# Generated from Kql.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .KqlParser import KqlParser
else:
    from KqlParser import KqlParser

# This class defines a complete listener for a parse tree produced by KqlParser.
class KqlListener(ParseTreeListener):

    # Enter a parse tree produced by KqlParser#top.
    def enterTop(self, ctx:KqlParser.TopContext):
        pass

    # Exit a parse tree produced by KqlParser#top.
    def exitTop(self, ctx:KqlParser.TopContext):
        pass


    # Enter a parse tree produced by KqlParser#query.
    def enterQuery(self, ctx:KqlParser.QueryContext):
        pass

    # Exit a parse tree produced by KqlParser#query.
    def exitQuery(self, ctx:KqlParser.QueryContext):
        pass


    # Enter a parse tree produced by KqlParser#statement.
    def enterStatement(self, ctx:KqlParser.StatementContext):
        pass

    # Exit a parse tree produced by KqlParser#statement.
    def exitStatement(self, ctx:KqlParser.StatementContext):
        pass


    # Enter a parse tree produced by KqlParser#aliasDatabaseStatement.
    def enterAliasDatabaseStatement(self, ctx:KqlParser.AliasDatabaseStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#aliasDatabaseStatement.
    def exitAliasDatabaseStatement(self, ctx:KqlParser.AliasDatabaseStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#letStatement.
    def enterLetStatement(self, ctx:KqlParser.LetStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#letStatement.
    def exitLetStatement(self, ctx:KqlParser.LetStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#letVariableDeclaration.
    def enterLetVariableDeclaration(self, ctx:KqlParser.LetVariableDeclarationContext):
        pass

    # Exit a parse tree produced by KqlParser#letVariableDeclaration.
    def exitLetVariableDeclaration(self, ctx:KqlParser.LetVariableDeclarationContext):
        pass


    # Enter a parse tree produced by KqlParser#letFunctionDeclaration.
    def enterLetFunctionDeclaration(self, ctx:KqlParser.LetFunctionDeclarationContext):
        pass

    # Exit a parse tree produced by KqlParser#letFunctionDeclaration.
    def exitLetFunctionDeclaration(self, ctx:KqlParser.LetFunctionDeclarationContext):
        pass


    # Enter a parse tree produced by KqlParser#letViewDeclaration.
    def enterLetViewDeclaration(self, ctx:KqlParser.LetViewDeclarationContext):
        pass

    # Exit a parse tree produced by KqlParser#letViewDeclaration.
    def exitLetViewDeclaration(self, ctx:KqlParser.LetViewDeclarationContext):
        pass


    # Enter a parse tree produced by KqlParser#letViewParameterList.
    def enterLetViewParameterList(self, ctx:KqlParser.LetViewParameterListContext):
        pass

    # Exit a parse tree produced by KqlParser#letViewParameterList.
    def exitLetViewParameterList(self, ctx:KqlParser.LetViewParameterListContext):
        pass


    # Enter a parse tree produced by KqlParser#letMaterializeDeclaration.
    def enterLetMaterializeDeclaration(self, ctx:KqlParser.LetMaterializeDeclarationContext):
        pass

    # Exit a parse tree produced by KqlParser#letMaterializeDeclaration.
    def exitLetMaterializeDeclaration(self, ctx:KqlParser.LetMaterializeDeclarationContext):
        pass


    # Enter a parse tree produced by KqlParser#letEntityGroupDeclaration.
    def enterLetEntityGroupDeclaration(self, ctx:KqlParser.LetEntityGroupDeclarationContext):
        pass

    # Exit a parse tree produced by KqlParser#letEntityGroupDeclaration.
    def exitLetEntityGroupDeclaration(self, ctx:KqlParser.LetEntityGroupDeclarationContext):
        pass


    # Enter a parse tree produced by KqlParser#letFunctionParameterList.
    def enterLetFunctionParameterList(self, ctx:KqlParser.LetFunctionParameterListContext):
        pass

    # Exit a parse tree produced by KqlParser#letFunctionParameterList.
    def exitLetFunctionParameterList(self, ctx:KqlParser.LetFunctionParameterListContext):
        pass


    # Enter a parse tree produced by KqlParser#scalarParameter.
    def enterScalarParameter(self, ctx:KqlParser.ScalarParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#scalarParameter.
    def exitScalarParameter(self, ctx:KqlParser.ScalarParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#scalarParameterDefault.
    def enterScalarParameterDefault(self, ctx:KqlParser.ScalarParameterDefaultContext):
        pass

    # Exit a parse tree produced by KqlParser#scalarParameterDefault.
    def exitScalarParameterDefault(self, ctx:KqlParser.ScalarParameterDefaultContext):
        pass


    # Enter a parse tree produced by KqlParser#tabularParameter.
    def enterTabularParameter(self, ctx:KqlParser.TabularParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#tabularParameter.
    def exitTabularParameter(self, ctx:KqlParser.TabularParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#tabularParameterOpenSchema.
    def enterTabularParameterOpenSchema(self, ctx:KqlParser.TabularParameterOpenSchemaContext):
        pass

    # Exit a parse tree produced by KqlParser#tabularParameterOpenSchema.
    def exitTabularParameterOpenSchema(self, ctx:KqlParser.TabularParameterOpenSchemaContext):
        pass


    # Enter a parse tree produced by KqlParser#tabularParameterRowSchema.
    def enterTabularParameterRowSchema(self, ctx:KqlParser.TabularParameterRowSchemaContext):
        pass

    # Exit a parse tree produced by KqlParser#tabularParameterRowSchema.
    def exitTabularParameterRowSchema(self, ctx:KqlParser.TabularParameterRowSchemaContext):
        pass


    # Enter a parse tree produced by KqlParser#tabularParameterRowSchemaColumnDeclaration.
    def enterTabularParameterRowSchemaColumnDeclaration(self, ctx:KqlParser.TabularParameterRowSchemaColumnDeclarationContext):
        pass

    # Exit a parse tree produced by KqlParser#tabularParameterRowSchemaColumnDeclaration.
    def exitTabularParameterRowSchemaColumnDeclaration(self, ctx:KqlParser.TabularParameterRowSchemaColumnDeclarationContext):
        pass


    # Enter a parse tree produced by KqlParser#letFunctionBody.
    def enterLetFunctionBody(self, ctx:KqlParser.LetFunctionBodyContext):
        pass

    # Exit a parse tree produced by KqlParser#letFunctionBody.
    def exitLetFunctionBody(self, ctx:KqlParser.LetFunctionBodyContext):
        pass


    # Enter a parse tree produced by KqlParser#letFunctionBodyStatement.
    def enterLetFunctionBodyStatement(self, ctx:KqlParser.LetFunctionBodyStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#letFunctionBodyStatement.
    def exitLetFunctionBodyStatement(self, ctx:KqlParser.LetFunctionBodyStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternStatement.
    def enterDeclarePatternStatement(self, ctx:KqlParser.DeclarePatternStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternStatement.
    def exitDeclarePatternStatement(self, ctx:KqlParser.DeclarePatternStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternDefinition.
    def enterDeclarePatternDefinition(self, ctx:KqlParser.DeclarePatternDefinitionContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternDefinition.
    def exitDeclarePatternDefinition(self, ctx:KqlParser.DeclarePatternDefinitionContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternParameterList.
    def enterDeclarePatternParameterList(self, ctx:KqlParser.DeclarePatternParameterListContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternParameterList.
    def exitDeclarePatternParameterList(self, ctx:KqlParser.DeclarePatternParameterListContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternParameter.
    def enterDeclarePatternParameter(self, ctx:KqlParser.DeclarePatternParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternParameter.
    def exitDeclarePatternParameter(self, ctx:KqlParser.DeclarePatternParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternPathParameter.
    def enterDeclarePatternPathParameter(self, ctx:KqlParser.DeclarePatternPathParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternPathParameter.
    def exitDeclarePatternPathParameter(self, ctx:KqlParser.DeclarePatternPathParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternRule.
    def enterDeclarePatternRule(self, ctx:KqlParser.DeclarePatternRuleContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternRule.
    def exitDeclarePatternRule(self, ctx:KqlParser.DeclarePatternRuleContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternRuleArgumentList.
    def enterDeclarePatternRuleArgumentList(self, ctx:KqlParser.DeclarePatternRuleArgumentListContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternRuleArgumentList.
    def exitDeclarePatternRuleArgumentList(self, ctx:KqlParser.DeclarePatternRuleArgumentListContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternRulePathArgument.
    def enterDeclarePatternRulePathArgument(self, ctx:KqlParser.DeclarePatternRulePathArgumentContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternRulePathArgument.
    def exitDeclarePatternRulePathArgument(self, ctx:KqlParser.DeclarePatternRulePathArgumentContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternRuleArgument.
    def enterDeclarePatternRuleArgument(self, ctx:KqlParser.DeclarePatternRuleArgumentContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternRuleArgument.
    def exitDeclarePatternRuleArgument(self, ctx:KqlParser.DeclarePatternRuleArgumentContext):
        pass


    # Enter a parse tree produced by KqlParser#declarePatternBody.
    def enterDeclarePatternBody(self, ctx:KqlParser.DeclarePatternBodyContext):
        pass

    # Exit a parse tree produced by KqlParser#declarePatternBody.
    def exitDeclarePatternBody(self, ctx:KqlParser.DeclarePatternBodyContext):
        pass


    # Enter a parse tree produced by KqlParser#restrictAccessStatement.
    def enterRestrictAccessStatement(self, ctx:KqlParser.RestrictAccessStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#restrictAccessStatement.
    def exitRestrictAccessStatement(self, ctx:KqlParser.RestrictAccessStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#restrictAccessStatementEntity.
    def enterRestrictAccessStatementEntity(self, ctx:KqlParser.RestrictAccessStatementEntityContext):
        pass

    # Exit a parse tree produced by KqlParser#restrictAccessStatementEntity.
    def exitRestrictAccessStatementEntity(self, ctx:KqlParser.RestrictAccessStatementEntityContext):
        pass


    # Enter a parse tree produced by KqlParser#setStatement.
    def enterSetStatement(self, ctx:KqlParser.SetStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#setStatement.
    def exitSetStatement(self, ctx:KqlParser.SetStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#setStatementOptionValue.
    def enterSetStatementOptionValue(self, ctx:KqlParser.SetStatementOptionValueContext):
        pass

    # Exit a parse tree produced by KqlParser#setStatementOptionValue.
    def exitSetStatementOptionValue(self, ctx:KqlParser.SetStatementOptionValueContext):
        pass


    # Enter a parse tree produced by KqlParser#declareQueryParametersStatement.
    def enterDeclareQueryParametersStatement(self, ctx:KqlParser.DeclareQueryParametersStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#declareQueryParametersStatement.
    def exitDeclareQueryParametersStatement(self, ctx:KqlParser.DeclareQueryParametersStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#declareQueryParametersStatementParameter.
    def enterDeclareQueryParametersStatementParameter(self, ctx:KqlParser.DeclareQueryParametersStatementParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#declareQueryParametersStatementParameter.
    def exitDeclareQueryParametersStatementParameter(self, ctx:KqlParser.DeclareQueryParametersStatementParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#queryStatement.
    def enterQueryStatement(self, ctx:KqlParser.QueryStatementContext):
        pass

    # Exit a parse tree produced by KqlParser#queryStatement.
    def exitQueryStatement(self, ctx:KqlParser.QueryStatementContext):
        pass


    # Enter a parse tree produced by KqlParser#expression.
    def enterExpression(self, ctx:KqlParser.ExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#expression.
    def exitExpression(self, ctx:KqlParser.ExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#pipeExpression.
    def enterPipeExpression(self, ctx:KqlParser.PipeExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#pipeExpression.
    def exitPipeExpression(self, ctx:KqlParser.PipeExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#pipedOperator.
    def enterPipedOperator(self, ctx:KqlParser.PipedOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#pipedOperator.
    def exitPipedOperator(self, ctx:KqlParser.PipedOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#pipeSubExpression.
    def enterPipeSubExpression(self, ctx:KqlParser.PipeSubExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#pipeSubExpression.
    def exitPipeSubExpression(self, ctx:KqlParser.PipeSubExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#beforePipeExpression.
    def enterBeforePipeExpression(self, ctx:KqlParser.BeforePipeExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#beforePipeExpression.
    def exitBeforePipeExpression(self, ctx:KqlParser.BeforePipeExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#afterPipeOperator.
    def enterAfterPipeOperator(self, ctx:KqlParser.AfterPipeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#afterPipeOperator.
    def exitAfterPipeOperator(self, ctx:KqlParser.AfterPipeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#beforeOrAfterPipeOperator.
    def enterBeforeOrAfterPipeOperator(self, ctx:KqlParser.BeforeOrAfterPipeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#beforeOrAfterPipeOperator.
    def exitBeforeOrAfterPipeOperator(self, ctx:KqlParser.BeforeOrAfterPipeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#forkPipeOperator.
    def enterForkPipeOperator(self, ctx:KqlParser.ForkPipeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#forkPipeOperator.
    def exitForkPipeOperator(self, ctx:KqlParser.ForkPipeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#asOperator.
    def enterAsOperator(self, ctx:KqlParser.AsOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#asOperator.
    def exitAsOperator(self, ctx:KqlParser.AsOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#assertSchemaOperator.
    def enterAssertSchemaOperator(self, ctx:KqlParser.AssertSchemaOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#assertSchemaOperator.
    def exitAssertSchemaOperator(self, ctx:KqlParser.AssertSchemaOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#consumeOperator.
    def enterConsumeOperator(self, ctx:KqlParser.ConsumeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#consumeOperator.
    def exitConsumeOperator(self, ctx:KqlParser.ConsumeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#countOperator.
    def enterCountOperator(self, ctx:KqlParser.CountOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#countOperator.
    def exitCountOperator(self, ctx:KqlParser.CountOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#countOperatorAsClause.
    def enterCountOperatorAsClause(self, ctx:KqlParser.CountOperatorAsClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#countOperatorAsClause.
    def exitCountOperatorAsClause(self, ctx:KqlParser.CountOperatorAsClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#distinctOperator.
    def enterDistinctOperator(self, ctx:KqlParser.DistinctOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#distinctOperator.
    def exitDistinctOperator(self, ctx:KqlParser.DistinctOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#distinctOperatorStarTarget.
    def enterDistinctOperatorStarTarget(self, ctx:KqlParser.DistinctOperatorStarTargetContext):
        pass

    # Exit a parse tree produced by KqlParser#distinctOperatorStarTarget.
    def exitDistinctOperatorStarTarget(self, ctx:KqlParser.DistinctOperatorStarTargetContext):
        pass


    # Enter a parse tree produced by KqlParser#distinctOperatorColumnListTarget.
    def enterDistinctOperatorColumnListTarget(self, ctx:KqlParser.DistinctOperatorColumnListTargetContext):
        pass

    # Exit a parse tree produced by KqlParser#distinctOperatorColumnListTarget.
    def exitDistinctOperatorColumnListTarget(self, ctx:KqlParser.DistinctOperatorColumnListTargetContext):
        pass


    # Enter a parse tree produced by KqlParser#evaluateOperator.
    def enterEvaluateOperator(self, ctx:KqlParser.EvaluateOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#evaluateOperator.
    def exitEvaluateOperator(self, ctx:KqlParser.EvaluateOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#evaluateOperatorSchemaClause.
    def enterEvaluateOperatorSchemaClause(self, ctx:KqlParser.EvaluateOperatorSchemaClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#evaluateOperatorSchemaClause.
    def exitEvaluateOperatorSchemaClause(self, ctx:KqlParser.EvaluateOperatorSchemaClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#extendOperator.
    def enterExtendOperator(self, ctx:KqlParser.ExtendOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#extendOperator.
    def exitExtendOperator(self, ctx:KqlParser.ExtendOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#executeAndCacheOperator.
    def enterExecuteAndCacheOperator(self, ctx:KqlParser.ExecuteAndCacheOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#executeAndCacheOperator.
    def exitExecuteAndCacheOperator(self, ctx:KqlParser.ExecuteAndCacheOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#facetByOperator.
    def enterFacetByOperator(self, ctx:KqlParser.FacetByOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#facetByOperator.
    def exitFacetByOperator(self, ctx:KqlParser.FacetByOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#facetByOperatorWithOperatorClause.
    def enterFacetByOperatorWithOperatorClause(self, ctx:KqlParser.FacetByOperatorWithOperatorClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#facetByOperatorWithOperatorClause.
    def exitFacetByOperatorWithOperatorClause(self, ctx:KqlParser.FacetByOperatorWithOperatorClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#facetByOperatorWithExpressionClause.
    def enterFacetByOperatorWithExpressionClause(self, ctx:KqlParser.FacetByOperatorWithExpressionClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#facetByOperatorWithExpressionClause.
    def exitFacetByOperatorWithExpressionClause(self, ctx:KqlParser.FacetByOperatorWithExpressionClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperator.
    def enterFindOperator(self, ctx:KqlParser.FindOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperator.
    def exitFindOperator(self, ctx:KqlParser.FindOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorParametersWhereClause.
    def enterFindOperatorParametersWhereClause(self, ctx:KqlParser.FindOperatorParametersWhereClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorParametersWhereClause.
    def exitFindOperatorParametersWhereClause(self, ctx:KqlParser.FindOperatorParametersWhereClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorInClause.
    def enterFindOperatorInClause(self, ctx:KqlParser.FindOperatorInClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorInClause.
    def exitFindOperatorInClause(self, ctx:KqlParser.FindOperatorInClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorProjectClause.
    def enterFindOperatorProjectClause(self, ctx:KqlParser.FindOperatorProjectClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorProjectClause.
    def exitFindOperatorProjectClause(self, ctx:KqlParser.FindOperatorProjectClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorProjectExpression.
    def enterFindOperatorProjectExpression(self, ctx:KqlParser.FindOperatorProjectExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorProjectExpression.
    def exitFindOperatorProjectExpression(self, ctx:KqlParser.FindOperatorProjectExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorColumnExpression.
    def enterFindOperatorColumnExpression(self, ctx:KqlParser.FindOperatorColumnExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorColumnExpression.
    def exitFindOperatorColumnExpression(self, ctx:KqlParser.FindOperatorColumnExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorOptionalColumnType.
    def enterFindOperatorOptionalColumnType(self, ctx:KqlParser.FindOperatorOptionalColumnTypeContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorOptionalColumnType.
    def exitFindOperatorOptionalColumnType(self, ctx:KqlParser.FindOperatorOptionalColumnTypeContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorPackExpression.
    def enterFindOperatorPackExpression(self, ctx:KqlParser.FindOperatorPackExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorPackExpression.
    def exitFindOperatorPackExpression(self, ctx:KqlParser.FindOperatorPackExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorProjectSmartClause.
    def enterFindOperatorProjectSmartClause(self, ctx:KqlParser.FindOperatorProjectSmartClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorProjectSmartClause.
    def exitFindOperatorProjectSmartClause(self, ctx:KqlParser.FindOperatorProjectSmartClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorProjectAwayClause.
    def enterFindOperatorProjectAwayClause(self, ctx:KqlParser.FindOperatorProjectAwayClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorProjectAwayClause.
    def exitFindOperatorProjectAwayClause(self, ctx:KqlParser.FindOperatorProjectAwayClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorProjectAwayStar.
    def enterFindOperatorProjectAwayStar(self, ctx:KqlParser.FindOperatorProjectAwayStarContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorProjectAwayStar.
    def exitFindOperatorProjectAwayStar(self, ctx:KqlParser.FindOperatorProjectAwayStarContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorProjectAwayColumnList.
    def enterFindOperatorProjectAwayColumnList(self, ctx:KqlParser.FindOperatorProjectAwayColumnListContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorProjectAwayColumnList.
    def exitFindOperatorProjectAwayColumnList(self, ctx:KqlParser.FindOperatorProjectAwayColumnListContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorSource.
    def enterFindOperatorSource(self, ctx:KqlParser.FindOperatorSourceContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorSource.
    def exitFindOperatorSource(self, ctx:KqlParser.FindOperatorSourceContext):
        pass


    # Enter a parse tree produced by KqlParser#findOperatorSourceEntityExpression.
    def enterFindOperatorSourceEntityExpression(self, ctx:KqlParser.FindOperatorSourceEntityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#findOperatorSourceEntityExpression.
    def exitFindOperatorSourceEntityExpression(self, ctx:KqlParser.FindOperatorSourceEntityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#forkOperator.
    def enterForkOperator(self, ctx:KqlParser.ForkOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#forkOperator.
    def exitForkOperator(self, ctx:KqlParser.ForkOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#forkOperatorFork.
    def enterForkOperatorFork(self, ctx:KqlParser.ForkOperatorForkContext):
        pass

    # Exit a parse tree produced by KqlParser#forkOperatorFork.
    def exitForkOperatorFork(self, ctx:KqlParser.ForkOperatorForkContext):
        pass


    # Enter a parse tree produced by KqlParser#forkOperatorExpressionName.
    def enterForkOperatorExpressionName(self, ctx:KqlParser.ForkOperatorExpressionNameContext):
        pass

    # Exit a parse tree produced by KqlParser#forkOperatorExpressionName.
    def exitForkOperatorExpressionName(self, ctx:KqlParser.ForkOperatorExpressionNameContext):
        pass


    # Enter a parse tree produced by KqlParser#forkOperatorExpression.
    def enterForkOperatorExpression(self, ctx:KqlParser.ForkOperatorExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#forkOperatorExpression.
    def exitForkOperatorExpression(self, ctx:KqlParser.ForkOperatorExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#forkOperatorPipedOperator.
    def enterForkOperatorPipedOperator(self, ctx:KqlParser.ForkOperatorPipedOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#forkOperatorPipedOperator.
    def exitForkOperatorPipedOperator(self, ctx:KqlParser.ForkOperatorPipedOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#getSchemaOperator.
    def enterGetSchemaOperator(self, ctx:KqlParser.GetSchemaOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#getSchemaOperator.
    def exitGetSchemaOperator(self, ctx:KqlParser.GetSchemaOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMarkComponentsOperator.
    def enterGraphMarkComponentsOperator(self, ctx:KqlParser.GraphMarkComponentsOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMarkComponentsOperator.
    def exitGraphMarkComponentsOperator(self, ctx:KqlParser.GraphMarkComponentsOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchOperator.
    def enterGraphMatchOperator(self, ctx:KqlParser.GraphMatchOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchOperator.
    def exitGraphMatchOperator(self, ctx:KqlParser.GraphMatchOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchPattern.
    def enterGraphMatchPattern(self, ctx:KqlParser.GraphMatchPatternContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchPattern.
    def exitGraphMatchPattern(self, ctx:KqlParser.GraphMatchPatternContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchPatternNode.
    def enterGraphMatchPatternNode(self, ctx:KqlParser.GraphMatchPatternNodeContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchPatternNode.
    def exitGraphMatchPatternNode(self, ctx:KqlParser.GraphMatchPatternNodeContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchPatternUnnamedEdge.
    def enterGraphMatchPatternUnnamedEdge(self, ctx:KqlParser.GraphMatchPatternUnnamedEdgeContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchPatternUnnamedEdge.
    def exitGraphMatchPatternUnnamedEdge(self, ctx:KqlParser.GraphMatchPatternUnnamedEdgeContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchPatternNamedEdge.
    def enterGraphMatchPatternNamedEdge(self, ctx:KqlParser.GraphMatchPatternNamedEdgeContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchPatternNamedEdge.
    def exitGraphMatchPatternNamedEdge(self, ctx:KqlParser.GraphMatchPatternNamedEdgeContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchPatternRange.
    def enterGraphMatchPatternRange(self, ctx:KqlParser.GraphMatchPatternRangeContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchPatternRange.
    def exitGraphMatchPatternRange(self, ctx:KqlParser.GraphMatchPatternRangeContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchWhereClause.
    def enterGraphMatchWhereClause(self, ctx:KqlParser.GraphMatchWhereClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchWhereClause.
    def exitGraphMatchWhereClause(self, ctx:KqlParser.GraphMatchWhereClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#graphMatchProjectClause.
    def enterGraphMatchProjectClause(self, ctx:KqlParser.GraphMatchProjectClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#graphMatchProjectClause.
    def exitGraphMatchProjectClause(self, ctx:KqlParser.GraphMatchProjectClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#graphToTableOperator.
    def enterGraphToTableOperator(self, ctx:KqlParser.GraphToTableOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#graphToTableOperator.
    def exitGraphToTableOperator(self, ctx:KqlParser.GraphToTableOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#graphToTableOutput.
    def enterGraphToTableOutput(self, ctx:KqlParser.GraphToTableOutputContext):
        pass

    # Exit a parse tree produced by KqlParser#graphToTableOutput.
    def exitGraphToTableOutput(self, ctx:KqlParser.GraphToTableOutputContext):
        pass


    # Enter a parse tree produced by KqlParser#graphToTableAsClause.
    def enterGraphToTableAsClause(self, ctx:KqlParser.GraphToTableAsClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#graphToTableAsClause.
    def exitGraphToTableAsClause(self, ctx:KqlParser.GraphToTableAsClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#graphShortestPathsOperator.
    def enterGraphShortestPathsOperator(self, ctx:KqlParser.GraphShortestPathsOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#graphShortestPathsOperator.
    def exitGraphShortestPathsOperator(self, ctx:KqlParser.GraphShortestPathsOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#invokeOperator.
    def enterInvokeOperator(self, ctx:KqlParser.InvokeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#invokeOperator.
    def exitInvokeOperator(self, ctx:KqlParser.InvokeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#joinOperator.
    def enterJoinOperator(self, ctx:KqlParser.JoinOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#joinOperator.
    def exitJoinOperator(self, ctx:KqlParser.JoinOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#joinOperatorOnClause.
    def enterJoinOperatorOnClause(self, ctx:KqlParser.JoinOperatorOnClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#joinOperatorOnClause.
    def exitJoinOperatorOnClause(self, ctx:KqlParser.JoinOperatorOnClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#joinOperatorWhereClause.
    def enterJoinOperatorWhereClause(self, ctx:KqlParser.JoinOperatorWhereClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#joinOperatorWhereClause.
    def exitJoinOperatorWhereClause(self, ctx:KqlParser.JoinOperatorWhereClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#lookupOperator.
    def enterLookupOperator(self, ctx:KqlParser.LookupOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#lookupOperator.
    def exitLookupOperator(self, ctx:KqlParser.LookupOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#macroExpandOperator.
    def enterMacroExpandOperator(self, ctx:KqlParser.MacroExpandOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#macroExpandOperator.
    def exitMacroExpandOperator(self, ctx:KqlParser.MacroExpandOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#macroExpandEntityGroup.
    def enterMacroExpandEntityGroup(self, ctx:KqlParser.MacroExpandEntityGroupContext):
        pass

    # Exit a parse tree produced by KqlParser#macroExpandEntityGroup.
    def exitMacroExpandEntityGroup(self, ctx:KqlParser.MacroExpandEntityGroupContext):
        pass


    # Enter a parse tree produced by KqlParser#entityGroupExpression.
    def enterEntityGroupExpression(self, ctx:KqlParser.EntityGroupExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#entityGroupExpression.
    def exitEntityGroupExpression(self, ctx:KqlParser.EntityGroupExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#makeGraphOperator.
    def enterMakeGraphOperator(self, ctx:KqlParser.MakeGraphOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#makeGraphOperator.
    def exitMakeGraphOperator(self, ctx:KqlParser.MakeGraphOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#makeGraphIdClause.
    def enterMakeGraphIdClause(self, ctx:KqlParser.MakeGraphIdClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeGraphIdClause.
    def exitMakeGraphIdClause(self, ctx:KqlParser.MakeGraphIdClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#makeGraphTablesAndKeysClause.
    def enterMakeGraphTablesAndKeysClause(self, ctx:KqlParser.MakeGraphTablesAndKeysClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeGraphTablesAndKeysClause.
    def exitMakeGraphTablesAndKeysClause(self, ctx:KqlParser.MakeGraphTablesAndKeysClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#makeGraphPartitionedByClause.
    def enterMakeGraphPartitionedByClause(self, ctx:KqlParser.MakeGraphPartitionedByClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeGraphPartitionedByClause.
    def exitMakeGraphPartitionedByClause(self, ctx:KqlParser.MakeGraphPartitionedByClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#makeSeriesOperator.
    def enterMakeSeriesOperator(self, ctx:KqlParser.MakeSeriesOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#makeSeriesOperator.
    def exitMakeSeriesOperator(self, ctx:KqlParser.MakeSeriesOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#makeSeriesOperatorOnClause.
    def enterMakeSeriesOperatorOnClause(self, ctx:KqlParser.MakeSeriesOperatorOnClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeSeriesOperatorOnClause.
    def exitMakeSeriesOperatorOnClause(self, ctx:KqlParser.MakeSeriesOperatorOnClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#makeSeriesOperatorAggregation.
    def enterMakeSeriesOperatorAggregation(self, ctx:KqlParser.MakeSeriesOperatorAggregationContext):
        pass

    # Exit a parse tree produced by KqlParser#makeSeriesOperatorAggregation.
    def exitMakeSeriesOperatorAggregation(self, ctx:KqlParser.MakeSeriesOperatorAggregationContext):
        pass


    # Enter a parse tree produced by KqlParser#makeSeriesOperatorExpressionDefaultClause.
    def enterMakeSeriesOperatorExpressionDefaultClause(self, ctx:KqlParser.MakeSeriesOperatorExpressionDefaultClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeSeriesOperatorExpressionDefaultClause.
    def exitMakeSeriesOperatorExpressionDefaultClause(self, ctx:KqlParser.MakeSeriesOperatorExpressionDefaultClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#makeSeriesOperatorInRangeClause.
    def enterMakeSeriesOperatorInRangeClause(self, ctx:KqlParser.MakeSeriesOperatorInRangeClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeSeriesOperatorInRangeClause.
    def exitMakeSeriesOperatorInRangeClause(self, ctx:KqlParser.MakeSeriesOperatorInRangeClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#makeSeriesOperatorFromToStepClause.
    def enterMakeSeriesOperatorFromToStepClause(self, ctx:KqlParser.MakeSeriesOperatorFromToStepClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeSeriesOperatorFromToStepClause.
    def exitMakeSeriesOperatorFromToStepClause(self, ctx:KqlParser.MakeSeriesOperatorFromToStepClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#makeSeriesOperatorByClause.
    def enterMakeSeriesOperatorByClause(self, ctx:KqlParser.MakeSeriesOperatorByClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#makeSeriesOperatorByClause.
    def exitMakeSeriesOperatorByClause(self, ctx:KqlParser.MakeSeriesOperatorByClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#mvapplyOperator.
    def enterMvapplyOperator(self, ctx:KqlParser.MvapplyOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#mvapplyOperator.
    def exitMvapplyOperator(self, ctx:KqlParser.MvapplyOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#mvapplyOperatorLimitClause.
    def enterMvapplyOperatorLimitClause(self, ctx:KqlParser.MvapplyOperatorLimitClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#mvapplyOperatorLimitClause.
    def exitMvapplyOperatorLimitClause(self, ctx:KqlParser.MvapplyOperatorLimitClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#mvapplyOperatorIdClause.
    def enterMvapplyOperatorIdClause(self, ctx:KqlParser.MvapplyOperatorIdClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#mvapplyOperatorIdClause.
    def exitMvapplyOperatorIdClause(self, ctx:KqlParser.MvapplyOperatorIdClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#mvapplyOperatorExpression.
    def enterMvapplyOperatorExpression(self, ctx:KqlParser.MvapplyOperatorExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#mvapplyOperatorExpression.
    def exitMvapplyOperatorExpression(self, ctx:KqlParser.MvapplyOperatorExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#mvapplyOperatorExpressionToClause.
    def enterMvapplyOperatorExpressionToClause(self, ctx:KqlParser.MvapplyOperatorExpressionToClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#mvapplyOperatorExpressionToClause.
    def exitMvapplyOperatorExpressionToClause(self, ctx:KqlParser.MvapplyOperatorExpressionToClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#mvexpandOperator.
    def enterMvexpandOperator(self, ctx:KqlParser.MvexpandOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#mvexpandOperator.
    def exitMvexpandOperator(self, ctx:KqlParser.MvexpandOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#mvexpandOperatorExpression.
    def enterMvexpandOperatorExpression(self, ctx:KqlParser.MvexpandOperatorExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#mvexpandOperatorExpression.
    def exitMvexpandOperatorExpression(self, ctx:KqlParser.MvexpandOperatorExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#parseOperator.
    def enterParseOperator(self, ctx:KqlParser.ParseOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#parseOperator.
    def exitParseOperator(self, ctx:KqlParser.ParseOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#parseOperatorKindClause.
    def enterParseOperatorKindClause(self, ctx:KqlParser.ParseOperatorKindClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#parseOperatorKindClause.
    def exitParseOperatorKindClause(self, ctx:KqlParser.ParseOperatorKindClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#parseOperatorFlagsClause.
    def enterParseOperatorFlagsClause(self, ctx:KqlParser.ParseOperatorFlagsClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#parseOperatorFlagsClause.
    def exitParseOperatorFlagsClause(self, ctx:KqlParser.ParseOperatorFlagsClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#parseOperatorNameAndOptionalType.
    def enterParseOperatorNameAndOptionalType(self, ctx:KqlParser.ParseOperatorNameAndOptionalTypeContext):
        pass

    # Exit a parse tree produced by KqlParser#parseOperatorNameAndOptionalType.
    def exitParseOperatorNameAndOptionalType(self, ctx:KqlParser.ParseOperatorNameAndOptionalTypeContext):
        pass


    # Enter a parse tree produced by KqlParser#parseOperatorPattern.
    def enterParseOperatorPattern(self, ctx:KqlParser.ParseOperatorPatternContext):
        pass

    # Exit a parse tree produced by KqlParser#parseOperatorPattern.
    def exitParseOperatorPattern(self, ctx:KqlParser.ParseOperatorPatternContext):
        pass


    # Enter a parse tree produced by KqlParser#parseOperatorPatternSegment.
    def enterParseOperatorPatternSegment(self, ctx:KqlParser.ParseOperatorPatternSegmentContext):
        pass

    # Exit a parse tree produced by KqlParser#parseOperatorPatternSegment.
    def exitParseOperatorPatternSegment(self, ctx:KqlParser.ParseOperatorPatternSegmentContext):
        pass


    # Enter a parse tree produced by KqlParser#parseWhereOperator.
    def enterParseWhereOperator(self, ctx:KqlParser.ParseWhereOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#parseWhereOperator.
    def exitParseWhereOperator(self, ctx:KqlParser.ParseWhereOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#parseKvOperator.
    def enterParseKvOperator(self, ctx:KqlParser.ParseKvOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#parseKvOperator.
    def exitParseKvOperator(self, ctx:KqlParser.ParseKvOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#parseKvWithClause.
    def enterParseKvWithClause(self, ctx:KqlParser.ParseKvWithClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#parseKvWithClause.
    def exitParseKvWithClause(self, ctx:KqlParser.ParseKvWithClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#partitionOperator.
    def enterPartitionOperator(self, ctx:KqlParser.PartitionOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#partitionOperator.
    def exitPartitionOperator(self, ctx:KqlParser.PartitionOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#partitionOperatorInClause.
    def enterPartitionOperatorInClause(self, ctx:KqlParser.PartitionOperatorInClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#partitionOperatorInClause.
    def exitPartitionOperatorInClause(self, ctx:KqlParser.PartitionOperatorInClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#partitionOperatorSubExpressionBody.
    def enterPartitionOperatorSubExpressionBody(self, ctx:KqlParser.PartitionOperatorSubExpressionBodyContext):
        pass

    # Exit a parse tree produced by KqlParser#partitionOperatorSubExpressionBody.
    def exitPartitionOperatorSubExpressionBody(self, ctx:KqlParser.PartitionOperatorSubExpressionBodyContext):
        pass


    # Enter a parse tree produced by KqlParser#partitionOperatorFullExpressionBody.
    def enterPartitionOperatorFullExpressionBody(self, ctx:KqlParser.PartitionOperatorFullExpressionBodyContext):
        pass

    # Exit a parse tree produced by KqlParser#partitionOperatorFullExpressionBody.
    def exitPartitionOperatorFullExpressionBody(self, ctx:KqlParser.PartitionOperatorFullExpressionBodyContext):
        pass


    # Enter a parse tree produced by KqlParser#partitionByOperator.
    def enterPartitionByOperator(self, ctx:KqlParser.PartitionByOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#partitionByOperator.
    def exitPartitionByOperator(self, ctx:KqlParser.PartitionByOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#partitionByOperatorIdClause.
    def enterPartitionByOperatorIdClause(self, ctx:KqlParser.PartitionByOperatorIdClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#partitionByOperatorIdClause.
    def exitPartitionByOperatorIdClause(self, ctx:KqlParser.PartitionByOperatorIdClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#printOperator.
    def enterPrintOperator(self, ctx:KqlParser.PrintOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#printOperator.
    def exitPrintOperator(self, ctx:KqlParser.PrintOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#projectAwayOperator.
    def enterProjectAwayOperator(self, ctx:KqlParser.ProjectAwayOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#projectAwayOperator.
    def exitProjectAwayOperator(self, ctx:KqlParser.ProjectAwayOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#projectKeepOperator.
    def enterProjectKeepOperator(self, ctx:KqlParser.ProjectKeepOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#projectKeepOperator.
    def exitProjectKeepOperator(self, ctx:KqlParser.ProjectKeepOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#projectOperator.
    def enterProjectOperator(self, ctx:KqlParser.ProjectOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#projectOperator.
    def exitProjectOperator(self, ctx:KqlParser.ProjectOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#projectRenameOperator.
    def enterProjectRenameOperator(self, ctx:KqlParser.ProjectRenameOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#projectRenameOperator.
    def exitProjectRenameOperator(self, ctx:KqlParser.ProjectRenameOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#projectReorderOperator.
    def enterProjectReorderOperator(self, ctx:KqlParser.ProjectReorderOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#projectReorderOperator.
    def exitProjectReorderOperator(self, ctx:KqlParser.ProjectReorderOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#projectReorderExpression.
    def enterProjectReorderExpression(self, ctx:KqlParser.ProjectReorderExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#projectReorderExpression.
    def exitProjectReorderExpression(self, ctx:KqlParser.ProjectReorderExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#reduceByOperator.
    def enterReduceByOperator(self, ctx:KqlParser.ReduceByOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#reduceByOperator.
    def exitReduceByOperator(self, ctx:KqlParser.ReduceByOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#reduceByWithClause.
    def enterReduceByWithClause(self, ctx:KqlParser.ReduceByWithClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#reduceByWithClause.
    def exitReduceByWithClause(self, ctx:KqlParser.ReduceByWithClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#renderOperator.
    def enterRenderOperator(self, ctx:KqlParser.RenderOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#renderOperator.
    def exitRenderOperator(self, ctx:KqlParser.RenderOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#renderOperatorWithClause.
    def enterRenderOperatorWithClause(self, ctx:KqlParser.RenderOperatorWithClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#renderOperatorWithClause.
    def exitRenderOperatorWithClause(self, ctx:KqlParser.RenderOperatorWithClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#renderOperatorLegacyPropertyList.
    def enterRenderOperatorLegacyPropertyList(self, ctx:KqlParser.RenderOperatorLegacyPropertyListContext):
        pass

    # Exit a parse tree produced by KqlParser#renderOperatorLegacyPropertyList.
    def exitRenderOperatorLegacyPropertyList(self, ctx:KqlParser.RenderOperatorLegacyPropertyListContext):
        pass


    # Enter a parse tree produced by KqlParser#renderOperatorProperty.
    def enterRenderOperatorProperty(self, ctx:KqlParser.RenderOperatorPropertyContext):
        pass

    # Exit a parse tree produced by KqlParser#renderOperatorProperty.
    def exitRenderOperatorProperty(self, ctx:KqlParser.RenderOperatorPropertyContext):
        pass


    # Enter a parse tree produced by KqlParser#renderPropertyNameList.
    def enterRenderPropertyNameList(self, ctx:KqlParser.RenderPropertyNameListContext):
        pass

    # Exit a parse tree produced by KqlParser#renderPropertyNameList.
    def exitRenderPropertyNameList(self, ctx:KqlParser.RenderPropertyNameListContext):
        pass


    # Enter a parse tree produced by KqlParser#renderOperatorLegacyProperty.
    def enterRenderOperatorLegacyProperty(self, ctx:KqlParser.RenderOperatorLegacyPropertyContext):
        pass

    # Exit a parse tree produced by KqlParser#renderOperatorLegacyProperty.
    def exitRenderOperatorLegacyProperty(self, ctx:KqlParser.RenderOperatorLegacyPropertyContext):
        pass


    # Enter a parse tree produced by KqlParser#sampleDistinctOperator.
    def enterSampleDistinctOperator(self, ctx:KqlParser.SampleDistinctOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#sampleDistinctOperator.
    def exitSampleDistinctOperator(self, ctx:KqlParser.SampleDistinctOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#sampleOperator.
    def enterSampleOperator(self, ctx:KqlParser.SampleOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#sampleOperator.
    def exitSampleOperator(self, ctx:KqlParser.SampleOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperator.
    def enterScanOperator(self, ctx:KqlParser.ScanOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperator.
    def exitScanOperator(self, ctx:KqlParser.ScanOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperatorOrderByClause.
    def enterScanOperatorOrderByClause(self, ctx:KqlParser.ScanOperatorOrderByClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperatorOrderByClause.
    def exitScanOperatorOrderByClause(self, ctx:KqlParser.ScanOperatorOrderByClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperatorPartitionByClause.
    def enterScanOperatorPartitionByClause(self, ctx:KqlParser.ScanOperatorPartitionByClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperatorPartitionByClause.
    def exitScanOperatorPartitionByClause(self, ctx:KqlParser.ScanOperatorPartitionByClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperatorDeclareClause.
    def enterScanOperatorDeclareClause(self, ctx:KqlParser.ScanOperatorDeclareClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperatorDeclareClause.
    def exitScanOperatorDeclareClause(self, ctx:KqlParser.ScanOperatorDeclareClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperatorStep.
    def enterScanOperatorStep(self, ctx:KqlParser.ScanOperatorStepContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperatorStep.
    def exitScanOperatorStep(self, ctx:KqlParser.ScanOperatorStepContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperatorStepOutputClause.
    def enterScanOperatorStepOutputClause(self, ctx:KqlParser.ScanOperatorStepOutputClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperatorStepOutputClause.
    def exitScanOperatorStepOutputClause(self, ctx:KqlParser.ScanOperatorStepOutputClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperatorBody.
    def enterScanOperatorBody(self, ctx:KqlParser.ScanOperatorBodyContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperatorBody.
    def exitScanOperatorBody(self, ctx:KqlParser.ScanOperatorBodyContext):
        pass


    # Enter a parse tree produced by KqlParser#scanOperatorAssignment.
    def enterScanOperatorAssignment(self, ctx:KqlParser.ScanOperatorAssignmentContext):
        pass

    # Exit a parse tree produced by KqlParser#scanOperatorAssignment.
    def exitScanOperatorAssignment(self, ctx:KqlParser.ScanOperatorAssignmentContext):
        pass


    # Enter a parse tree produced by KqlParser#searchOperator.
    def enterSearchOperator(self, ctx:KqlParser.SearchOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#searchOperator.
    def exitSearchOperator(self, ctx:KqlParser.SearchOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#searchOperatorStarAndExpression.
    def enterSearchOperatorStarAndExpression(self, ctx:KqlParser.SearchOperatorStarAndExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#searchOperatorStarAndExpression.
    def exitSearchOperatorStarAndExpression(self, ctx:KqlParser.SearchOperatorStarAndExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#searchOperatorInClause.
    def enterSearchOperatorInClause(self, ctx:KqlParser.SearchOperatorInClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#searchOperatorInClause.
    def exitSearchOperatorInClause(self, ctx:KqlParser.SearchOperatorInClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#serializeOperator.
    def enterSerializeOperator(self, ctx:KqlParser.SerializeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#serializeOperator.
    def exitSerializeOperator(self, ctx:KqlParser.SerializeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#sortOperator.
    def enterSortOperator(self, ctx:KqlParser.SortOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#sortOperator.
    def exitSortOperator(self, ctx:KqlParser.SortOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#orderedExpression.
    def enterOrderedExpression(self, ctx:KqlParser.OrderedExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#orderedExpression.
    def exitOrderedExpression(self, ctx:KqlParser.OrderedExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#sortOrdering.
    def enterSortOrdering(self, ctx:KqlParser.SortOrderingContext):
        pass

    # Exit a parse tree produced by KqlParser#sortOrdering.
    def exitSortOrdering(self, ctx:KqlParser.SortOrderingContext):
        pass


    # Enter a parse tree produced by KqlParser#summarizeOperator.
    def enterSummarizeOperator(self, ctx:KqlParser.SummarizeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#summarizeOperator.
    def exitSummarizeOperator(self, ctx:KqlParser.SummarizeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#summarizeOperatorByClause.
    def enterSummarizeOperatorByClause(self, ctx:KqlParser.SummarizeOperatorByClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#summarizeOperatorByClause.
    def exitSummarizeOperatorByClause(self, ctx:KqlParser.SummarizeOperatorByClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#summarizeOperatorLegacyBinClause.
    def enterSummarizeOperatorLegacyBinClause(self, ctx:KqlParser.SummarizeOperatorLegacyBinClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#summarizeOperatorLegacyBinClause.
    def exitSummarizeOperatorLegacyBinClause(self, ctx:KqlParser.SummarizeOperatorLegacyBinClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#takeOperator.
    def enterTakeOperator(self, ctx:KqlParser.TakeOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#takeOperator.
    def exitTakeOperator(self, ctx:KqlParser.TakeOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#topOperator.
    def enterTopOperator(self, ctx:KqlParser.TopOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#topOperator.
    def exitTopOperator(self, ctx:KqlParser.TopOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#topHittersOperator.
    def enterTopHittersOperator(self, ctx:KqlParser.TopHittersOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#topHittersOperator.
    def exitTopHittersOperator(self, ctx:KqlParser.TopHittersOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#topHittersOperatorByClause.
    def enterTopHittersOperatorByClause(self, ctx:KqlParser.TopHittersOperatorByClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#topHittersOperatorByClause.
    def exitTopHittersOperatorByClause(self, ctx:KqlParser.TopHittersOperatorByClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#topNestedOperator.
    def enterTopNestedOperator(self, ctx:KqlParser.TopNestedOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#topNestedOperator.
    def exitTopNestedOperator(self, ctx:KqlParser.TopNestedOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#topNestedOperatorPart.
    def enterTopNestedOperatorPart(self, ctx:KqlParser.TopNestedOperatorPartContext):
        pass

    # Exit a parse tree produced by KqlParser#topNestedOperatorPart.
    def exitTopNestedOperatorPart(self, ctx:KqlParser.TopNestedOperatorPartContext):
        pass


    # Enter a parse tree produced by KqlParser#topNestedOperatorWithOthersClause.
    def enterTopNestedOperatorWithOthersClause(self, ctx:KqlParser.TopNestedOperatorWithOthersClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#topNestedOperatorWithOthersClause.
    def exitTopNestedOperatorWithOthersClause(self, ctx:KqlParser.TopNestedOperatorWithOthersClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#unionOperator.
    def enterUnionOperator(self, ctx:KqlParser.UnionOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#unionOperator.
    def exitUnionOperator(self, ctx:KqlParser.UnionOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#unionOperatorExpression.
    def enterUnionOperatorExpression(self, ctx:KqlParser.UnionOperatorExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#unionOperatorExpression.
    def exitUnionOperatorExpression(self, ctx:KqlParser.UnionOperatorExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#whereOperator.
    def enterWhereOperator(self, ctx:KqlParser.WhereOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#whereOperator.
    def exitWhereOperator(self, ctx:KqlParser.WhereOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#contextualSubExpression.
    def enterContextualSubExpression(self, ctx:KqlParser.ContextualSubExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#contextualSubExpression.
    def exitContextualSubExpression(self, ctx:KqlParser.ContextualSubExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#contextualPipeExpression.
    def enterContextualPipeExpression(self, ctx:KqlParser.ContextualPipeExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#contextualPipeExpression.
    def exitContextualPipeExpression(self, ctx:KqlParser.ContextualPipeExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#contextualPipeExpressionPipedOperator.
    def enterContextualPipeExpressionPipedOperator(self, ctx:KqlParser.ContextualPipeExpressionPipedOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#contextualPipeExpressionPipedOperator.
    def exitContextualPipeExpressionPipedOperator(self, ctx:KqlParser.ContextualPipeExpressionPipedOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#strictQueryOperatorParameter.
    def enterStrictQueryOperatorParameter(self, ctx:KqlParser.StrictQueryOperatorParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#strictQueryOperatorParameter.
    def exitStrictQueryOperatorParameter(self, ctx:KqlParser.StrictQueryOperatorParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#relaxedQueryOperatorParameter.
    def enterRelaxedQueryOperatorParameter(self, ctx:KqlParser.RelaxedQueryOperatorParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#relaxedQueryOperatorParameter.
    def exitRelaxedQueryOperatorParameter(self, ctx:KqlParser.RelaxedQueryOperatorParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#queryOperatorProperty.
    def enterQueryOperatorProperty(self, ctx:KqlParser.QueryOperatorPropertyContext):
        pass

    # Exit a parse tree produced by KqlParser#queryOperatorProperty.
    def exitQueryOperatorProperty(self, ctx:KqlParser.QueryOperatorPropertyContext):
        pass


    # Enter a parse tree produced by KqlParser#namedExpression.
    def enterNamedExpression(self, ctx:KqlParser.NamedExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#namedExpression.
    def exitNamedExpression(self, ctx:KqlParser.NamedExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#namedExpressionNameClause.
    def enterNamedExpressionNameClause(self, ctx:KqlParser.NamedExpressionNameClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#namedExpressionNameClause.
    def exitNamedExpressionNameClause(self, ctx:KqlParser.NamedExpressionNameClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#namedExpressionNameList.
    def enterNamedExpressionNameList(self, ctx:KqlParser.NamedExpressionNameListContext):
        pass

    # Exit a parse tree produced by KqlParser#namedExpressionNameList.
    def exitNamedExpressionNameList(self, ctx:KqlParser.NamedExpressionNameListContext):
        pass


    # Enter a parse tree produced by KqlParser#scopedFunctionCallExpression.
    def enterScopedFunctionCallExpression(self, ctx:KqlParser.ScopedFunctionCallExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#scopedFunctionCallExpression.
    def exitScopedFunctionCallExpression(self, ctx:KqlParser.ScopedFunctionCallExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#unnamedExpression.
    def enterUnnamedExpression(self, ctx:KqlParser.UnnamedExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#unnamedExpression.
    def exitUnnamedExpression(self, ctx:KqlParser.UnnamedExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#logicalOrExpression.
    def enterLogicalOrExpression(self, ctx:KqlParser.LogicalOrExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#logicalOrExpression.
    def exitLogicalOrExpression(self, ctx:KqlParser.LogicalOrExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#logicalOrOperation.
    def enterLogicalOrOperation(self, ctx:KqlParser.LogicalOrOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#logicalOrOperation.
    def exitLogicalOrOperation(self, ctx:KqlParser.LogicalOrOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#logicalAndExpression.
    def enterLogicalAndExpression(self, ctx:KqlParser.LogicalAndExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#logicalAndExpression.
    def exitLogicalAndExpression(self, ctx:KqlParser.LogicalAndExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#logicalAndOperation.
    def enterLogicalAndOperation(self, ctx:KqlParser.LogicalAndOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#logicalAndOperation.
    def exitLogicalAndOperation(self, ctx:KqlParser.LogicalAndOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#equalityExpression.
    def enterEqualityExpression(self, ctx:KqlParser.EqualityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#equalityExpression.
    def exitEqualityExpression(self, ctx:KqlParser.EqualityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#equalsEqualityExpression.
    def enterEqualsEqualityExpression(self, ctx:KqlParser.EqualsEqualityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#equalsEqualityExpression.
    def exitEqualsEqualityExpression(self, ctx:KqlParser.EqualsEqualityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#listEqualityExpression.
    def enterListEqualityExpression(self, ctx:KqlParser.ListEqualityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#listEqualityExpression.
    def exitListEqualityExpression(self, ctx:KqlParser.ListEqualityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#betweenEqualityExpression.
    def enterBetweenEqualityExpression(self, ctx:KqlParser.BetweenEqualityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#betweenEqualityExpression.
    def exitBetweenEqualityExpression(self, ctx:KqlParser.BetweenEqualityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#starEqualityExpression.
    def enterStarEqualityExpression(self, ctx:KqlParser.StarEqualityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#starEqualityExpression.
    def exitStarEqualityExpression(self, ctx:KqlParser.StarEqualityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#relationalExpression.
    def enterRelationalExpression(self, ctx:KqlParser.RelationalExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#relationalExpression.
    def exitRelationalExpression(self, ctx:KqlParser.RelationalExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#additiveExpression.
    def enterAdditiveExpression(self, ctx:KqlParser.AdditiveExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#additiveExpression.
    def exitAdditiveExpression(self, ctx:KqlParser.AdditiveExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#additiveOperation.
    def enterAdditiveOperation(self, ctx:KqlParser.AdditiveOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#additiveOperation.
    def exitAdditiveOperation(self, ctx:KqlParser.AdditiveOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#multiplicativeExpression.
    def enterMultiplicativeExpression(self, ctx:KqlParser.MultiplicativeExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#multiplicativeExpression.
    def exitMultiplicativeExpression(self, ctx:KqlParser.MultiplicativeExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#multiplicativeOperation.
    def enterMultiplicativeOperation(self, ctx:KqlParser.MultiplicativeOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#multiplicativeOperation.
    def exitMultiplicativeOperation(self, ctx:KqlParser.MultiplicativeOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#stringOperatorExpression.
    def enterStringOperatorExpression(self, ctx:KqlParser.StringOperatorExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#stringOperatorExpression.
    def exitStringOperatorExpression(self, ctx:KqlParser.StringOperatorExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#stringBinaryOperatorExpression.
    def enterStringBinaryOperatorExpression(self, ctx:KqlParser.StringBinaryOperatorExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#stringBinaryOperatorExpression.
    def exitStringBinaryOperatorExpression(self, ctx:KqlParser.StringBinaryOperatorExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#stringBinaryOperation.
    def enterStringBinaryOperation(self, ctx:KqlParser.StringBinaryOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#stringBinaryOperation.
    def exitStringBinaryOperation(self, ctx:KqlParser.StringBinaryOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#stringBinaryOperator.
    def enterStringBinaryOperator(self, ctx:KqlParser.StringBinaryOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#stringBinaryOperator.
    def exitStringBinaryOperator(self, ctx:KqlParser.StringBinaryOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#stringStarOperatorExpression.
    def enterStringStarOperatorExpression(self, ctx:KqlParser.StringStarOperatorExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#stringStarOperatorExpression.
    def exitStringStarOperatorExpression(self, ctx:KqlParser.StringStarOperatorExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#invocationExpression.
    def enterInvocationExpression(self, ctx:KqlParser.InvocationExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#invocationExpression.
    def exitInvocationExpression(self, ctx:KqlParser.InvocationExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#functionCallOrPathExpression.
    def enterFunctionCallOrPathExpression(self, ctx:KqlParser.FunctionCallOrPathExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#functionCallOrPathExpression.
    def exitFunctionCallOrPathExpression(self, ctx:KqlParser.FunctionCallOrPathExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#functionCallOrPathRoot.
    def enterFunctionCallOrPathRoot(self, ctx:KqlParser.FunctionCallOrPathRootContext):
        pass

    # Exit a parse tree produced by KqlParser#functionCallOrPathRoot.
    def exitFunctionCallOrPathRoot(self, ctx:KqlParser.FunctionCallOrPathRootContext):
        pass


    # Enter a parse tree produced by KqlParser#functionCallOrPathPathExpression.
    def enterFunctionCallOrPathPathExpression(self, ctx:KqlParser.FunctionCallOrPathPathExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#functionCallOrPathPathExpression.
    def exitFunctionCallOrPathPathExpression(self, ctx:KqlParser.FunctionCallOrPathPathExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#functionCallOrPathOperation.
    def enterFunctionCallOrPathOperation(self, ctx:KqlParser.FunctionCallOrPathOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#functionCallOrPathOperation.
    def exitFunctionCallOrPathOperation(self, ctx:KqlParser.FunctionCallOrPathOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#functionCallOrPathPathOperation.
    def enterFunctionCallOrPathPathOperation(self, ctx:KqlParser.FunctionCallOrPathPathOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#functionCallOrPathPathOperation.
    def exitFunctionCallOrPathPathOperation(self, ctx:KqlParser.FunctionCallOrPathPathOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#functionCallOrPathElementOperation.
    def enterFunctionCallOrPathElementOperation(self, ctx:KqlParser.FunctionCallOrPathElementOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#functionCallOrPathElementOperation.
    def exitFunctionCallOrPathElementOperation(self, ctx:KqlParser.FunctionCallOrPathElementOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#legacyFunctionCallOrPathElementOperation.
    def enterLegacyFunctionCallOrPathElementOperation(self, ctx:KqlParser.LegacyFunctionCallOrPathElementOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#legacyFunctionCallOrPathElementOperation.
    def exitLegacyFunctionCallOrPathElementOperation(self, ctx:KqlParser.LegacyFunctionCallOrPathElementOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#toScalarExpression.
    def enterToScalarExpression(self, ctx:KqlParser.ToScalarExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#toScalarExpression.
    def exitToScalarExpression(self, ctx:KqlParser.ToScalarExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#toTableExpression.
    def enterToTableExpression(self, ctx:KqlParser.ToTableExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#toTableExpression.
    def exitToTableExpression(self, ctx:KqlParser.ToTableExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#noOptimizationParameter.
    def enterNoOptimizationParameter(self, ctx:KqlParser.NoOptimizationParameterContext):
        pass

    # Exit a parse tree produced by KqlParser#noOptimizationParameter.
    def exitNoOptimizationParameter(self, ctx:KqlParser.NoOptimizationParameterContext):
        pass


    # Enter a parse tree produced by KqlParser#dotCompositeFunctionCallExpression.
    def enterDotCompositeFunctionCallExpression(self, ctx:KqlParser.DotCompositeFunctionCallExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#dotCompositeFunctionCallExpression.
    def exitDotCompositeFunctionCallExpression(self, ctx:KqlParser.DotCompositeFunctionCallExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#dotCompositeFunctionCallOperation.
    def enterDotCompositeFunctionCallOperation(self, ctx:KqlParser.DotCompositeFunctionCallOperationContext):
        pass

    # Exit a parse tree produced by KqlParser#dotCompositeFunctionCallOperation.
    def exitDotCompositeFunctionCallOperation(self, ctx:KqlParser.DotCompositeFunctionCallOperationContext):
        pass


    # Enter a parse tree produced by KqlParser#functionCallExpression.
    def enterFunctionCallExpression(self, ctx:KqlParser.FunctionCallExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#functionCallExpression.
    def exitFunctionCallExpression(self, ctx:KqlParser.FunctionCallExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#namedFunctionCallExpression.
    def enterNamedFunctionCallExpression(self, ctx:KqlParser.NamedFunctionCallExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#namedFunctionCallExpression.
    def exitNamedFunctionCallExpression(self, ctx:KqlParser.NamedFunctionCallExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#argumentExpression.
    def enterArgumentExpression(self, ctx:KqlParser.ArgumentExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#argumentExpression.
    def exitArgumentExpression(self, ctx:KqlParser.ArgumentExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#countExpression.
    def enterCountExpression(self, ctx:KqlParser.CountExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#countExpression.
    def exitCountExpression(self, ctx:KqlParser.CountExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#starExpression.
    def enterStarExpression(self, ctx:KqlParser.StarExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#starExpression.
    def exitStarExpression(self, ctx:KqlParser.StarExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#primaryExpression.
    def enterPrimaryExpression(self, ctx:KqlParser.PrimaryExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#primaryExpression.
    def exitPrimaryExpression(self, ctx:KqlParser.PrimaryExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#nameReferenceWithDataScope.
    def enterNameReferenceWithDataScope(self, ctx:KqlParser.NameReferenceWithDataScopeContext):
        pass

    # Exit a parse tree produced by KqlParser#nameReferenceWithDataScope.
    def exitNameReferenceWithDataScope(self, ctx:KqlParser.NameReferenceWithDataScopeContext):
        pass


    # Enter a parse tree produced by KqlParser#dataScopeClause.
    def enterDataScopeClause(self, ctx:KqlParser.DataScopeClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#dataScopeClause.
    def exitDataScopeClause(self, ctx:KqlParser.DataScopeClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#parenthesizedExpression.
    def enterParenthesizedExpression(self, ctx:KqlParser.ParenthesizedExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#parenthesizedExpression.
    def exitParenthesizedExpression(self, ctx:KqlParser.ParenthesizedExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#rangeExpression.
    def enterRangeExpression(self, ctx:KqlParser.RangeExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#rangeExpression.
    def exitRangeExpression(self, ctx:KqlParser.RangeExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#entityExpression.
    def enterEntityExpression(self, ctx:KqlParser.EntityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#entityExpression.
    def exitEntityExpression(self, ctx:KqlParser.EntityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#entityPathOrElementExpression.
    def enterEntityPathOrElementExpression(self, ctx:KqlParser.EntityPathOrElementExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#entityPathOrElementExpression.
    def exitEntityPathOrElementExpression(self, ctx:KqlParser.EntityPathOrElementExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#entityPathOrElementOperator.
    def enterEntityPathOrElementOperator(self, ctx:KqlParser.EntityPathOrElementOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#entityPathOrElementOperator.
    def exitEntityPathOrElementOperator(self, ctx:KqlParser.EntityPathOrElementOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#entityPathOperator.
    def enterEntityPathOperator(self, ctx:KqlParser.EntityPathOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#entityPathOperator.
    def exitEntityPathOperator(self, ctx:KqlParser.EntityPathOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#entityElementOperator.
    def enterEntityElementOperator(self, ctx:KqlParser.EntityElementOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#entityElementOperator.
    def exitEntityElementOperator(self, ctx:KqlParser.EntityElementOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#legacyEntityPathElementOperator.
    def enterLegacyEntityPathElementOperator(self, ctx:KqlParser.LegacyEntityPathElementOperatorContext):
        pass

    # Exit a parse tree produced by KqlParser#legacyEntityPathElementOperator.
    def exitLegacyEntityPathElementOperator(self, ctx:KqlParser.LegacyEntityPathElementOperatorContext):
        pass


    # Enter a parse tree produced by KqlParser#entityName.
    def enterEntityName(self, ctx:KqlParser.EntityNameContext):
        pass

    # Exit a parse tree produced by KqlParser#entityName.
    def exitEntityName(self, ctx:KqlParser.EntityNameContext):
        pass


    # Enter a parse tree produced by KqlParser#entityNameReference.
    def enterEntityNameReference(self, ctx:KqlParser.EntityNameReferenceContext):
        pass

    # Exit a parse tree produced by KqlParser#entityNameReference.
    def exitEntityNameReference(self, ctx:KqlParser.EntityNameReferenceContext):
        pass


    # Enter a parse tree produced by KqlParser#atSignName.
    def enterAtSignName(self, ctx:KqlParser.AtSignNameContext):
        pass

    # Exit a parse tree produced by KqlParser#atSignName.
    def exitAtSignName(self, ctx:KqlParser.AtSignNameContext):
        pass


    # Enter a parse tree produced by KqlParser#extendedPathName.
    def enterExtendedPathName(self, ctx:KqlParser.ExtendedPathNameContext):
        pass

    # Exit a parse tree produced by KqlParser#extendedPathName.
    def exitExtendedPathName(self, ctx:KqlParser.ExtendedPathNameContext):
        pass


    # Enter a parse tree produced by KqlParser#wildcardedEntityExpression.
    def enterWildcardedEntityExpression(self, ctx:KqlParser.WildcardedEntityExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#wildcardedEntityExpression.
    def exitWildcardedEntityExpression(self, ctx:KqlParser.WildcardedEntityExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#wildcardedPathExpression.
    def enterWildcardedPathExpression(self, ctx:KqlParser.WildcardedPathExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#wildcardedPathExpression.
    def exitWildcardedPathExpression(self, ctx:KqlParser.WildcardedPathExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#wildcardedPathName.
    def enterWildcardedPathName(self, ctx:KqlParser.WildcardedPathNameContext):
        pass

    # Exit a parse tree produced by KqlParser#wildcardedPathName.
    def exitWildcardedPathName(self, ctx:KqlParser.WildcardedPathNameContext):
        pass


    # Enter a parse tree produced by KqlParser#contextualDataTableExpression.
    def enterContextualDataTableExpression(self, ctx:KqlParser.ContextualDataTableExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#contextualDataTableExpression.
    def exitContextualDataTableExpression(self, ctx:KqlParser.ContextualDataTableExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#dataTableExpression.
    def enterDataTableExpression(self, ctx:KqlParser.DataTableExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#dataTableExpression.
    def exitDataTableExpression(self, ctx:KqlParser.DataTableExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#rowSchema.
    def enterRowSchema(self, ctx:KqlParser.RowSchemaContext):
        pass

    # Exit a parse tree produced by KqlParser#rowSchema.
    def exitRowSchema(self, ctx:KqlParser.RowSchemaContext):
        pass


    # Enter a parse tree produced by KqlParser#rowSchemaColumnDeclaration.
    def enterRowSchemaColumnDeclaration(self, ctx:KqlParser.RowSchemaColumnDeclarationContext):
        pass

    # Exit a parse tree produced by KqlParser#rowSchemaColumnDeclaration.
    def exitRowSchemaColumnDeclaration(self, ctx:KqlParser.RowSchemaColumnDeclarationContext):
        pass


    # Enter a parse tree produced by KqlParser#externalDataExpression.
    def enterExternalDataExpression(self, ctx:KqlParser.ExternalDataExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#externalDataExpression.
    def exitExternalDataExpression(self, ctx:KqlParser.ExternalDataExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#externalDataWithClause.
    def enterExternalDataWithClause(self, ctx:KqlParser.ExternalDataWithClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#externalDataWithClause.
    def exitExternalDataWithClause(self, ctx:KqlParser.ExternalDataWithClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#externalDataWithClauseProperty.
    def enterExternalDataWithClauseProperty(self, ctx:KqlParser.ExternalDataWithClausePropertyContext):
        pass

    # Exit a parse tree produced by KqlParser#externalDataWithClauseProperty.
    def exitExternalDataWithClauseProperty(self, ctx:KqlParser.ExternalDataWithClausePropertyContext):
        pass


    # Enter a parse tree produced by KqlParser#materializedViewCombineExpression.
    def enterMaterializedViewCombineExpression(self, ctx:KqlParser.MaterializedViewCombineExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#materializedViewCombineExpression.
    def exitMaterializedViewCombineExpression(self, ctx:KqlParser.MaterializedViewCombineExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#materializeViewCombineBaseClause.
    def enterMaterializeViewCombineBaseClause(self, ctx:KqlParser.MaterializeViewCombineBaseClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#materializeViewCombineBaseClause.
    def exitMaterializeViewCombineBaseClause(self, ctx:KqlParser.MaterializeViewCombineBaseClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#materializedViewCombineDeltaClause.
    def enterMaterializedViewCombineDeltaClause(self, ctx:KqlParser.MaterializedViewCombineDeltaClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#materializedViewCombineDeltaClause.
    def exitMaterializedViewCombineDeltaClause(self, ctx:KqlParser.MaterializedViewCombineDeltaClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#materializedViewCombineAggregationsClause.
    def enterMaterializedViewCombineAggregationsClause(self, ctx:KqlParser.MaterializedViewCombineAggregationsClauseContext):
        pass

    # Exit a parse tree produced by KqlParser#materializedViewCombineAggregationsClause.
    def exitMaterializedViewCombineAggregationsClause(self, ctx:KqlParser.MaterializedViewCombineAggregationsClauseContext):
        pass


    # Enter a parse tree produced by KqlParser#scalarType.
    def enterScalarType(self, ctx:KqlParser.ScalarTypeContext):
        pass

    # Exit a parse tree produced by KqlParser#scalarType.
    def exitScalarType(self, ctx:KqlParser.ScalarTypeContext):
        pass


    # Enter a parse tree produced by KqlParser#extendedScalarType.
    def enterExtendedScalarType(self, ctx:KqlParser.ExtendedScalarTypeContext):
        pass

    # Exit a parse tree produced by KqlParser#extendedScalarType.
    def exitExtendedScalarType(self, ctx:KqlParser.ExtendedScalarTypeContext):
        pass


    # Enter a parse tree produced by KqlParser#parameterName.
    def enterParameterName(self, ctx:KqlParser.ParameterNameContext):
        pass

    # Exit a parse tree produced by KqlParser#parameterName.
    def exitParameterName(self, ctx:KqlParser.ParameterNameContext):
        pass


    # Enter a parse tree produced by KqlParser#simpleNameReference.
    def enterSimpleNameReference(self, ctx:KqlParser.SimpleNameReferenceContext):
        pass

    # Exit a parse tree produced by KqlParser#simpleNameReference.
    def exitSimpleNameReference(self, ctx:KqlParser.SimpleNameReferenceContext):
        pass


    # Enter a parse tree produced by KqlParser#extendedNameReference.
    def enterExtendedNameReference(self, ctx:KqlParser.ExtendedNameReferenceContext):
        pass

    # Exit a parse tree produced by KqlParser#extendedNameReference.
    def exitExtendedNameReference(self, ctx:KqlParser.ExtendedNameReferenceContext):
        pass


    # Enter a parse tree produced by KqlParser#wildcardedNameReference.
    def enterWildcardedNameReference(self, ctx:KqlParser.WildcardedNameReferenceContext):
        pass

    # Exit a parse tree produced by KqlParser#wildcardedNameReference.
    def exitWildcardedNameReference(self, ctx:KqlParser.WildcardedNameReferenceContext):
        pass


    # Enter a parse tree produced by KqlParser#simpleOrWildcardedNameReference.
    def enterSimpleOrWildcardedNameReference(self, ctx:KqlParser.SimpleOrWildcardedNameReferenceContext):
        pass

    # Exit a parse tree produced by KqlParser#simpleOrWildcardedNameReference.
    def exitSimpleOrWildcardedNameReference(self, ctx:KqlParser.SimpleOrWildcardedNameReferenceContext):
        pass


    # Enter a parse tree produced by KqlParser#identifierName.
    def enterIdentifierName(self, ctx:KqlParser.IdentifierNameContext):
        pass

    # Exit a parse tree produced by KqlParser#identifierName.
    def exitIdentifierName(self, ctx:KqlParser.IdentifierNameContext):
        pass


    # Enter a parse tree produced by KqlParser#keywordName.
    def enterKeywordName(self, ctx:KqlParser.KeywordNameContext):
        pass

    # Exit a parse tree produced by KqlParser#keywordName.
    def exitKeywordName(self, ctx:KqlParser.KeywordNameContext):
        pass


    # Enter a parse tree produced by KqlParser#extendedKeywordName.
    def enterExtendedKeywordName(self, ctx:KqlParser.ExtendedKeywordNameContext):
        pass

    # Exit a parse tree produced by KqlParser#extendedKeywordName.
    def exitExtendedKeywordName(self, ctx:KqlParser.ExtendedKeywordNameContext):
        pass


    # Enter a parse tree produced by KqlParser#escapedName.
    def enterEscapedName(self, ctx:KqlParser.EscapedNameContext):
        pass

    # Exit a parse tree produced by KqlParser#escapedName.
    def exitEscapedName(self, ctx:KqlParser.EscapedNameContext):
        pass


    # Enter a parse tree produced by KqlParser#identifierOrKeywordName.
    def enterIdentifierOrKeywordName(self, ctx:KqlParser.IdentifierOrKeywordNameContext):
        pass

    # Exit a parse tree produced by KqlParser#identifierOrKeywordName.
    def exitIdentifierOrKeywordName(self, ctx:KqlParser.IdentifierOrKeywordNameContext):
        pass


    # Enter a parse tree produced by KqlParser#identifierOrKeywordOrEscapedName.
    def enterIdentifierOrKeywordOrEscapedName(self, ctx:KqlParser.IdentifierOrKeywordOrEscapedNameContext):
        pass

    # Exit a parse tree produced by KqlParser#identifierOrKeywordOrEscapedName.
    def exitIdentifierOrKeywordOrEscapedName(self, ctx:KqlParser.IdentifierOrKeywordOrEscapedNameContext):
        pass


    # Enter a parse tree produced by KqlParser#identifierOrExtendedKeywordOrEscapedName.
    def enterIdentifierOrExtendedKeywordOrEscapedName(self, ctx:KqlParser.IdentifierOrExtendedKeywordOrEscapedNameContext):
        pass

    # Exit a parse tree produced by KqlParser#identifierOrExtendedKeywordOrEscapedName.
    def exitIdentifierOrExtendedKeywordOrEscapedName(self, ctx:KqlParser.IdentifierOrExtendedKeywordOrEscapedNameContext):
        pass


    # Enter a parse tree produced by KqlParser#identifierOrExtendedKeywordName.
    def enterIdentifierOrExtendedKeywordName(self, ctx:KqlParser.IdentifierOrExtendedKeywordNameContext):
        pass

    # Exit a parse tree produced by KqlParser#identifierOrExtendedKeywordName.
    def exitIdentifierOrExtendedKeywordName(self, ctx:KqlParser.IdentifierOrExtendedKeywordNameContext):
        pass


    # Enter a parse tree produced by KqlParser#wildcardedName.
    def enterWildcardedName(self, ctx:KqlParser.WildcardedNameContext):
        pass

    # Exit a parse tree produced by KqlParser#wildcardedName.
    def exitWildcardedName(self, ctx:KqlParser.WildcardedNameContext):
        pass


    # Enter a parse tree produced by KqlParser#wildcardedNamePrefix.
    def enterWildcardedNamePrefix(self, ctx:KqlParser.WildcardedNamePrefixContext):
        pass

    # Exit a parse tree produced by KqlParser#wildcardedNamePrefix.
    def exitWildcardedNamePrefix(self, ctx:KqlParser.WildcardedNamePrefixContext):
        pass


    # Enter a parse tree produced by KqlParser#wildcardedNameSegment.
    def enterWildcardedNameSegment(self, ctx:KqlParser.WildcardedNameSegmentContext):
        pass

    # Exit a parse tree produced by KqlParser#wildcardedNameSegment.
    def exitWildcardedNameSegment(self, ctx:KqlParser.WildcardedNameSegmentContext):
        pass


    # Enter a parse tree produced by KqlParser#literalExpression.
    def enterLiteralExpression(self, ctx:KqlParser.LiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#literalExpression.
    def exitLiteralExpression(self, ctx:KqlParser.LiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#unsignedLiteralExpression.
    def enterUnsignedLiteralExpression(self, ctx:KqlParser.UnsignedLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#unsignedLiteralExpression.
    def exitUnsignedLiteralExpression(self, ctx:KqlParser.UnsignedLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#numberLikeLiteralExpression.
    def enterNumberLikeLiteralExpression(self, ctx:KqlParser.NumberLikeLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#numberLikeLiteralExpression.
    def exitNumberLikeLiteralExpression(self, ctx:KqlParser.NumberLikeLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#numericLiteralExpression.
    def enterNumericLiteralExpression(self, ctx:KqlParser.NumericLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#numericLiteralExpression.
    def exitNumericLiteralExpression(self, ctx:KqlParser.NumericLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#signedLiteralExpression.
    def enterSignedLiteralExpression(self, ctx:KqlParser.SignedLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#signedLiteralExpression.
    def exitSignedLiteralExpression(self, ctx:KqlParser.SignedLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#longLiteralExpression.
    def enterLongLiteralExpression(self, ctx:KqlParser.LongLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#longLiteralExpression.
    def exitLongLiteralExpression(self, ctx:KqlParser.LongLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#intLiteralExpression.
    def enterIntLiteralExpression(self, ctx:KqlParser.IntLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#intLiteralExpression.
    def exitIntLiteralExpression(self, ctx:KqlParser.IntLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#realLiteralExpression.
    def enterRealLiteralExpression(self, ctx:KqlParser.RealLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#realLiteralExpression.
    def exitRealLiteralExpression(self, ctx:KqlParser.RealLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#decimalLiteralExpression.
    def enterDecimalLiteralExpression(self, ctx:KqlParser.DecimalLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#decimalLiteralExpression.
    def exitDecimalLiteralExpression(self, ctx:KqlParser.DecimalLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#dateTimeLiteralExpression.
    def enterDateTimeLiteralExpression(self, ctx:KqlParser.DateTimeLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#dateTimeLiteralExpression.
    def exitDateTimeLiteralExpression(self, ctx:KqlParser.DateTimeLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#timeSpanLiteralExpression.
    def enterTimeSpanLiteralExpression(self, ctx:KqlParser.TimeSpanLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#timeSpanLiteralExpression.
    def exitTimeSpanLiteralExpression(self, ctx:KqlParser.TimeSpanLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#booleanLiteralExpression.
    def enterBooleanLiteralExpression(self, ctx:KqlParser.BooleanLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#booleanLiteralExpression.
    def exitBooleanLiteralExpression(self, ctx:KqlParser.BooleanLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#guidLiteralExpression.
    def enterGuidLiteralExpression(self, ctx:KqlParser.GuidLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#guidLiteralExpression.
    def exitGuidLiteralExpression(self, ctx:KqlParser.GuidLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#typeLiteralExpression.
    def enterTypeLiteralExpression(self, ctx:KqlParser.TypeLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#typeLiteralExpression.
    def exitTypeLiteralExpression(self, ctx:KqlParser.TypeLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#signedLongLiteralExpression.
    def enterSignedLongLiteralExpression(self, ctx:KqlParser.SignedLongLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#signedLongLiteralExpression.
    def exitSignedLongLiteralExpression(self, ctx:KqlParser.SignedLongLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#signedRealLiteralExpression.
    def enterSignedRealLiteralExpression(self, ctx:KqlParser.SignedRealLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#signedRealLiteralExpression.
    def exitSignedRealLiteralExpression(self, ctx:KqlParser.SignedRealLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#stringLiteralExpression.
    def enterStringLiteralExpression(self, ctx:KqlParser.StringLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#stringLiteralExpression.
    def exitStringLiteralExpression(self, ctx:KqlParser.StringLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#dynamicLiteralExpression.
    def enterDynamicLiteralExpression(self, ctx:KqlParser.DynamicLiteralExpressionContext):
        pass

    # Exit a parse tree produced by KqlParser#dynamicLiteralExpression.
    def exitDynamicLiteralExpression(self, ctx:KqlParser.DynamicLiteralExpressionContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonValue.
    def enterJsonValue(self, ctx:KqlParser.JsonValueContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonValue.
    def exitJsonValue(self, ctx:KqlParser.JsonValueContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonObject.
    def enterJsonObject(self, ctx:KqlParser.JsonObjectContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonObject.
    def exitJsonObject(self, ctx:KqlParser.JsonObjectContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonPair.
    def enterJsonPair(self, ctx:KqlParser.JsonPairContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonPair.
    def exitJsonPair(self, ctx:KqlParser.JsonPairContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonArray.
    def enterJsonArray(self, ctx:KqlParser.JsonArrayContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonArray.
    def exitJsonArray(self, ctx:KqlParser.JsonArrayContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonBoolean.
    def enterJsonBoolean(self, ctx:KqlParser.JsonBooleanContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonBoolean.
    def exitJsonBoolean(self, ctx:KqlParser.JsonBooleanContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonDateTime.
    def enterJsonDateTime(self, ctx:KqlParser.JsonDateTimeContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonDateTime.
    def exitJsonDateTime(self, ctx:KqlParser.JsonDateTimeContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonGuid.
    def enterJsonGuid(self, ctx:KqlParser.JsonGuidContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonGuid.
    def exitJsonGuid(self, ctx:KqlParser.JsonGuidContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonNull.
    def enterJsonNull(self, ctx:KqlParser.JsonNullContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonNull.
    def exitJsonNull(self, ctx:KqlParser.JsonNullContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonString.
    def enterJsonString(self, ctx:KqlParser.JsonStringContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonString.
    def exitJsonString(self, ctx:KqlParser.JsonStringContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonTimeSpan.
    def enterJsonTimeSpan(self, ctx:KqlParser.JsonTimeSpanContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonTimeSpan.
    def exitJsonTimeSpan(self, ctx:KqlParser.JsonTimeSpanContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonLong.
    def enterJsonLong(self, ctx:KqlParser.JsonLongContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonLong.
    def exitJsonLong(self, ctx:KqlParser.JsonLongContext):
        pass


    # Enter a parse tree produced by KqlParser#jsonReal.
    def enterJsonReal(self, ctx:KqlParser.JsonRealContext):
        pass

    # Exit a parse tree produced by KqlParser#jsonReal.
    def exitJsonReal(self, ctx:KqlParser.JsonRealContext):
        pass



del KqlParser